"""编排真实上传、普通读者问答与可审计的逐例评测采集。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
import logging
from pathlib import Path
import time

from .client import EvaluationError
from .dataset import load_cases

logger = logging.getLogger(__name__)


def run_pipeline(dataset_dir, output_dir, *, uploader, reader, concurrency=2, import_timeout=900):
    """全部语料同时入库，逐题独立会话，保留失败样本和真实上下文。"""
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError('concurrency_must_be_between_1_and_8')
    dataset_dir, output_dir = Path(dataset_dir), Path(output_dir)
    cases = load_cases(dataset_dir)
    manifest_bytes = (dataset_dir / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    upload_actor = uploader.request('GET', 'auth/me')
    read_actor = reader.request('GET', 'auth/me')
    if upload_actor['id'] == read_actor['id']:
        raise ValueError('uploader_and_reader_must_differ')
    if not {'knowledge:manage', 'knowledge:read'}.issubset(upload_actor['permissions']):
        raise ValueError('uploader_missing_permissions')
    if not {'knowledge:read', 'chat:ask'}.issubset(read_actor['permissions']) or 'knowledge:manage' in read_actor['permissions']:
        raise ValueError('reader_must_be_ordinary_knowledge_user')
    output_dir.mkdir(parents=True, exist_ok=False)
    pipeline = dict(dataset_source=manifest['source'], dataset_manifest_sha256=sha256(manifest_bytes).hexdigest(),
        seed=manifest['seed'], total=len(cases), completed=0, failed=0, status='running',
        base_url=uploader.base_url, uploader_id=upload_actor['id'], reader_id=read_actor['id'], concurrency=concurrency)
    samples = [None] * len(cases)
    started = time.perf_counter()
    with (output_dir / 'samples.jsonl').open('x', encoding='utf-8') as stream:
        try:
            task = uploader.upload(dataset_dir, manifest, [upload_actor['id'], read_actor['id']])
            pipeline['upload_ms'] = (time.perf_counter() - started) * 1000
            pipeline['task_id'] = task['id']
            logger.info('流程=RAG评测 步骤=上传完成 task_id=%s documents=%s', task['id'], len(manifest['documents']))
            task = uploader.wait_import(task['id'], timeout=import_timeout)
            pipeline['import_task'] = task
            pipeline['ingestion_ms'] = (time.perf_counter() - started) * 1000
            document_keys = {entry['path']: entry['document_id'] for entry in manifest['documents']}
            imported = {document_keys[item['relative_path']]: dict(knowledge_id=item['knowledge_id'], version_id=item['version_id']) for item in task['items']}
            if set(imported) != set(document_keys.values()):
                raise EvaluationError('import_document_set_mismatch')
            for item in imported.values():
                chunks = reader.request('GET', f"knowledge/{item['knowledge_id']}/chunks")
                if not chunks or any(chunk['version_id'] != item['version_id'] for chunk in chunks):
                    raise EvaluationError('published_chunks_missing')
            pipeline['published_documents'] = imported

            def collect(index, case):
                """调用真实用户问题，只有完整成功流和获准引用才作为评分输入。"""
                row = dict(case, user_input=case['question'], status='error', response='', retrieved_contexts=[])
                try:
                    row.update(reader.answer(case['question']))
                    row['status'] = 'complete'
                    row['expected_document'] = imported[case['document_id']]
                    row['expected_document_retrieved'] = any(ref['knowledge_id'] == row['expected_document']['knowledge_id'] for ref in row['citations'])
                except EvaluationError as error:
                    row.update(error=error.code, http_status=error.status, **error.evidence)
                except Exception as error:
                    row['error'] = 'collector_' + type(error).__name__
                logger.info('流程=RAG评测 步骤=问答结束 case_id=%s status=%s', case['id'], row['status'])
                return index, row

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(collect, index, case) for index, case in enumerate(cases)]
                for future in as_completed(futures):
                    index, row = future.result()
                    samples[index] = row
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                    stream.flush()
        except Exception as error:
            pipeline['error'] = error.code if isinstance(error, EvaluationError) else type(error).__name__
            if isinstance(error, EvaluationError):
                pipeline.update(error.evidence)
            for index, case in enumerate(cases):
                if samples[index] is None:
                    row = dict(case, user_input=case['question'], status='error', response='', retrieved_contexts=[], error='pipeline_' + pipeline['error'])
                    samples[index] = row
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            logger.error('流程=RAG评测 状态=失败 error=%s', pipeline['error'])
    pipeline['completed'] = sum(row['status'] == 'complete' for row in samples)
    pipeline['failed'] = len(samples) - pipeline['completed']
    pipeline['status'] = 'complete' if pipeline['failed'] == 0 else 'error'
    pipeline['elapsed_ms'] = (time.perf_counter() - started) * 1000
    with (output_dir / 'pipeline.json').open('x', encoding='utf-8') as stream:
        json.dump(pipeline, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return pipeline
