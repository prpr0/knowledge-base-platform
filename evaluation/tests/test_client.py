"""验证采集真实问答终态与实际引用上下文，禁止错误数据进入评审。"""
import importlib
import json

import httpx
import pytest


def client_module():
    """目标采集实现缺失时报告明确功能红测。"""
    try:
        return importlib.import_module('evaluation.client')
    except ModuleNotFoundError as error:
        if error.name == 'evaluation.client':
            pytest.fail('尚未实现真实平台上传问答采集器')
        raise


def sse_event(name, payload):
    """构造标准事件夹具，仅验证HTTP协议，不当作真实联调证据。"""
    return 'event: ' + name + '\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


@pytest.mark.parametrize('terminal', ['missing', 'error', 'contradictory'])
def test_stream_requires_successful_unambiguous_terminal(terminal):
    """已输出草稿但断流、错误或终态矛盾时不能产生成功评分样本。"""
    module = client_module()
    def handle(request):
        """隔离网络边界，故意提供不完整或失败流。"""
        if request.url.path == '/api/conversations':
            return httpx.Response(200, json=dict(id=1))
        body = sse_event('meta', dict(request_id='request', message_id=2, source_mode='external_api', model_version='deepseek-flash'))
        body += sse_event('delta', dict(text='草稿 [S1]'))
        if terminal != 'missing':
            body += sse_event('error', dict(code='502'))
        if terminal == 'contradictory':
            body += sse_event('done', dict(message_id=2, status='complete'))
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
    with module.PlatformClient('http://testserver', 'unit-token', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module.EvaluationError):
            client.answer('问题')


def test_collector_uses_actual_citations_and_checks_persisted_answer():
    """评测上下文从本轮答案引用读取，不能用独立检索或参考答案替换。"""
    module = client_module()
    citation = dict(label='S1', knowledge_id=1, version_id=3, chunk_id=4, title='原文', location='line:1', section='', page=None)
    text = '真实答案 [S1]'
    def handle(request):
        """返回实际引用端点与持久化记录协议，未声明路由一律失败。"""
        path = request.url.path
        if path == '/api/conversations':
            return httpx.Response(200, json=dict(id=1))
        if path == '/api/conversations/1/ask':
            assert json.loads(request.content)['question'] == '原始问题'
            events = [('meta', dict(request_id='request', message_id=2, source_mode='external_api', model_version='deepseek-flash')),
                      ('delta', dict(text=text)), ('citations', dict(items=[citation])),
                      ('usage', dict(tokens=dict(prompt_tokens=4, completion_tokens=2, total_tokens=6), known=True, latency_ms=1.)),
                      ('done', dict(message_id=2, status='complete'))]
            return httpx.Response(200, text=''.join(sse_event(name, value) for name, value in events), headers={'content-type': 'text/event-stream'})
        if path == '/api/conversations/1/messages':
            return httpx.Response(200, json=[dict(id=2, role='assistant', content=text, status='complete', citations=[citation], source_mode='external_api', model_version='deepseek-flash')])
        if path == '/api/citations/2/S1':
            return httpx.Response(200, json=dict(citation, content='这是实际送入模型的正文'))
        raise AssertionError('评测不应调用其他检索或模型路径')
    with module.PlatformClient('http://testserver', 'unit-token', transport=httpx.MockTransport(handle)) as client:
        result = client.answer('原始问题')
    assert result['response'] == text
    assert result['retrieved_contexts'] == ['这是实际送入模型的正文']
    assert result['request_id'] == 'request' and result['model_version'] == 'deepseek-flash'


def test_partial_import_is_failure_and_cannot_start_questions():
    """批次仅部分发布成功时阻止后续问答，不能静默漏掉失败文档。"""
    module = client_module()
    def handle(request):
        """固定返回真实接口定义的partial终态。"""
        return httpx.Response(200, json=dict(id='task', status='partial', completed=1, failed=1, items=[]))
    with module.PlatformClient('http://testserver', 'unit-token', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(module.EvaluationError):
            client.wait_import('task', timeout=1, interval=0)
