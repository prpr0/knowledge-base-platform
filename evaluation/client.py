"""通过真实平台HTTP接口采集上传状态、模型回答和本轮引用证据。"""
from contextlib import ExitStack
import json
from pathlib import Path
import time
from urllib.parse import quote, urlsplit

import httpx


class EvaluationError(RuntimeError):
    """只携带固定错误码、HTTP状态与非敏感取证信息。"""
    def __init__(self, code, status=None, evidence=None):
        """避免把供应商响应、密码或请求头写入错误报告。"""
        super().__init__(code)
        self.code, self.status, self.evidence = code, status, evidence or {}


def sse_events(lines):
    """解析多行data与跨网络块已解码的SSE，保留服务端事件名称。"""
    name, data = 'message', []
    for line in lines:
        if not line:
            if data:
                try:
                    value = json.loads('\n'.join(data))
                except ValueError:
                    raise EvaluationError('invalid_sse_json') from None
                if not isinstance(value, dict):
                    raise EvaluationError('invalid_sse_payload')
                yield name, value
            name, data = 'message', []
        elif line.startswith('event:'):
            name = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
    if data:
        raise EvaluationError('unterminated_sse_event')


class PlatformClient:
    """以单个真实账号访问平台，令牌仅驻留HTTP客户端内存。"""
    def __init__(self, base_url, token='', *, transport=None, timeout=600):
        """约束基础地址不含凭证，允许单元测试隔离HTTP边界。"""
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid_platform_url')
        self.base_url = base_url.rstrip('/')
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        self.http = httpx.Client(base_url=self.base_url + '/api/', headers=headers,
            timeout=httpx.Timeout(timeout, connect=10), transport=transport)

    def __enter__(self):
        """支持对HTTP连接池的显式生命周期管理。"""
        return self

    def __exit__(self, *args):
        """退出采集时关闭连接池。"""
        self.http.close()

    def request(self, method, path, **options):
        """统一抛出固定错误码，绝不回显认证头或原始响应正文。"""
        try:
            response = self.http.request(method, path.lstrip('/'), **options)
        except httpx.HTTPError:
            raise EvaluationError('platform_network_error') from None
        if not response.is_success:
            raise EvaluationError('platform_http_error', response.status_code)
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError:
            raise EvaluationError('invalid_platform_json') from None

    def login(self, username, password):
        """通过实际登录接口取得令牌，不将密码或令牌放进报告。"""
        result = self.request('POST', 'auth/login', json=dict(username=username, password=password))
        self.http.headers['Authorization'] = 'Bearer ' + result['access_token']
        return self.request('GET', 'auth/me')

    def upload(self, dataset_dir, manifest, user_ids):
        """批量上传真实文档，仅授予明确指定的上传者与普通读者。"""
        root = Path(dataset_dir).resolve()
        paths, files = [], []
        with ExitStack() as stack:
            for entry in manifest['documents']:
                path = (root / entry['path']).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise EvaluationError('invalid_upload_document')
                paths.append(entry['path'])
                files.append(('files', (path.name, stack.enter_context(path.open('rb')), 'text/markdown')))
            acl = [dict(type='user', entity_id=identifier) for identifier in sorted(set(user_ids))]
            return self.request('POST', 'imports', files=files,
                data=dict(paths=json.dumps(paths), acl=json.dumps(acl), category='CMRC2018 Ragas评测'))

    def wait_import(self, task_id, *, timeout=900, interval=1):
        """等待真实任务终态，部分失败或超时直接阻止后续评测。"""
        deadline = time.monotonic() + timeout
        while True:
            task = self.request('GET', 'imports/' + quote(task_id, safe=''))
            if task['status'] == 'succeeded':
                if not task['items'] or any(item['status'] != 'succeeded' or not item.get('version_id') for item in task['items']):
                    raise EvaluationError('invalid_import_success')
                return task
            if task['status'] in ('partial', 'failed'):
                raise EvaluationError('import_failed', evidence=dict(task_id=task_id, task=task))
            if time.monotonic() >= deadline:
                raise EvaluationError('import_timeout', evidence=dict(task_id=task_id, task=task))
            time.sleep(min(interval, max(0, deadline - time.monotonic())))

    def answer(self, question):
        """逐题新建会话并核对流、持久化答案和实际引用内容完全一致。"""
        started = time.perf_counter()
        conversation = self.request('POST', 'conversations', json=dict(title='CMRC Ragas评测'))
        identifier = conversation['id']
        evidence = dict(conversation_id=identifier)
        meta, citations, usage = None, None, None
        text, terminal, first_delta_ms = [], False, None
        try:
            with self.http.stream('POST', f'conversations/{identifier}/ask', json=dict(question=question)) as response:
                if not response.is_success:
                    raise EvaluationError('ask_http_error', response.status_code)
                if 'text/event-stream' not in response.headers.get('content-type', ''):
                    raise EvaluationError('invalid_stream_content_type')
                if response.headers.get('x-request-id'):
                    evidence['request_id'] = response.headers['x-request-id']
                for name, value in sse_events(response.iter_lines()):
                    if terminal:
                        raise EvaluationError('event_after_terminal')
                    if name == 'meta':
                        if meta is not None:
                            raise EvaluationError('duplicate_stream_meta')
                        meta = value
                        evidence.update(value)
                        evidence['first_meta_ms'] = (time.perf_counter() - started) * 1000
                    elif name == 'delta':
                        if meta is None or not isinstance(value.get('text'), str):
                            raise EvaluationError('invalid_stream_delta')
                        text.append(value['text'])
                        if first_delta_ms is None:
                            first_delta_ms = (time.perf_counter() - started) * 1000
                    elif name == 'citations':
                        if citations is not None or not isinstance(value.get('items'), list):
                            raise EvaluationError('invalid_stream_citations')
                        citations = value['items']
                    elif name == 'usage':
                        usage = value
                    elif name == 'error':
                        raise EvaluationError('ask_stream_error')
                    elif name == 'done':
                        if meta is None or value.get('message_id') != meta.get('message_id') or value.get('status') != 'complete':
                            raise EvaluationError('invalid_stream_terminal')
                        terminal = True
            if not terminal or meta is None or citations is None or usage is None:
                raise EvaluationError('incomplete_stream')
            if meta.get('source_mode') not in ('model', 'external_api') or not meta.get('model_version'):
                raise EvaluationError('not_a_model_answer')
            message_id = meta['message_id']
            messages = self.request('GET', f'conversations/{identifier}/messages')
            answer = next((row for row in messages if row['id'] == message_id and row['role'] == 'assistant'), None)
            content = ''.join(text)
            if (not answer or answer['status'] != 'complete' or not content.strip() or answer['content'] != content
                    or answer['citations'] != citations or answer['source_mode'] != meta['source_mode']
                    or answer['model_version'] != meta['model_version'] or not citations):
                raise EvaluationError('history_or_citation_mismatch')
            contexts, seen = [], set()
            for ref in citations:
                if ref['label'] in seen:
                    raise EvaluationError('duplicate_citation_label')
                seen.add(ref['label'])
                source = self.request('GET', f"citations/{message_id}/{quote(ref['label'], safe='')}")
                if any(source.get(key) != ref.get(key) for key in ('label', 'knowledge_id', 'version_id', 'chunk_id')) or not source.get('content'):
                    raise EvaluationError('citation_version_changed')
                contexts.append(source['content'])
            return dict(user_input=question, response=content, retrieved_contexts=contexts,
                citations=citations, token_usage=usage.get('tokens'), first_delta_ms=first_delta_ms,
                platform_latency_ms=usage.get('latency_ms'), elapsed_ms=(time.perf_counter() - started) * 1000, **evidence)
        except EvaluationError as error:
            error.evidence.update(evidence)
            raise
        except httpx.HTTPError:
            raise EvaluationError('ask_network_error', evidence=evidence) from None
