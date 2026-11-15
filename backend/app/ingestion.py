"""持久化导入任务、文件存储与显式失败重试，数据库状态始终为权威。"""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import logging
from fastapi import HTTPException
from .documents import DocumentError, normalize_relative_path
from .models import ImportTask, ImportTaskItem, Knowledge
from .services import build_grants

logger = logging.getLogger(__name__)
ALLOWED_FORMATS = {'pdf', 'docx', 'doc', 'md', 'txt'}


def storage_path(settings, key: str) -> Path:
    """物理路径只接受服务端生成的单层存储名。"""
    if not key or Path(key).name != key or '/' in key or '\\' in key:
        raise DocumentError('非法存储定位')
    root = settings.data_root.resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise DocumentError('文件定位越出存储目录')
    return path


def store_stream(settings, stream, file_format: str) -> tuple[str, str]:
    """限量流式写入原文件并计算SHA256，不使用客户端物理路径。"""
    settings.data_root.mkdir(parents=True, exist_ok=True)
    key = uuid4().hex + '.' + file_format
    target = storage_path(settings, key)
    digest, total = sha256(), 0
    try:
        with target.open('xb') as destination:
            while block := stream.read(1024 * 1024):
                total += len(block)
                if total > settings.max_upload_bytes:
                    raise HTTPException(413, '单文件超过当前配置的上传上限')
                digest.update(block)
                destination.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return key, digest.hexdigest()


def store_text(settings, content: str) -> tuple[str, str]:
    """人工编辑生成独立文本源文件，与旧原文件分离。"""
    from io import BytesIO
    return store_stream(settings, BytesIO(content.encode('utf-8')), 'txt')


def task_view(task: ImportTask) -> dict:
    """依据真实子项状态输出进度，不按时间伪造百分比。"""
    items = sorted(task.items, key=lambda item: item.relative_path)
    completed = sum(item.status == 'succeeded' for item in items)
    failed = sum(item.status == 'failed' for item in items)
    return dict(id=task.id, status=task.status, total=len(items), completed=completed, failed=failed,
                progress=round(sum(item.progress for item in items) / len(items)) if items else 0,
                created_at=task.created_at, updated_at=task.updated_at,
                items=[dict(id=item.id, relative_path=item.relative_path, status=item.status, stage=item.stage,
                            progress=item.progress, error=item.error, knowledge_id=item.knowledge_id,
                            version_id=item.version_id, attempt=item.attempt) for item in items])


def refresh_task(task: ImportTask) -> None:
    """从数据库子项重新计算父任务状态。"""
    states = [item.status for item in task.items]
    if any(state == 'running' for state in states):
        task.status = 'running'
    elif any(state == 'queued' for state in states):
        task.status = 'queued'
    elif states and all(state == 'succeeded' for state in states):
        task.status = 'succeeded'
    elif any(state == 'succeeded' for state in states):
        task.status = 'partial'
    else:
        task.status = 'failed'
    task.updated_at = datetime.now(timezone.utc)


def new_import(session, settings, requester_id: int, files, grants, category: str) -> ImportTask:
    """先验证全部相对路径，再为每个文件创建独立知识单位与任务子项。"""
    normalized = [normalize_relative_path(path) for path, stream in files]
    if len(set(normalized)) != len(normalized):
        raise HTTPException(422, '同一批次存在重复相对路径')
    formats = [Path(path).suffix.lower().lstrip('.') for path in normalized]
    if any(value not in ALLOWED_FORMATS for value in formats):
        raise HTTPException(422, '仅支持PDF、DOCX、DOC、Markdown和TXT')
    task = ImportTask(requester_id=requester_id, category=category, acl=[grant.model_dump() for grant in grants])
    session.add(task)
    written = []
    try:
        for (original, stream), path, file_format in zip(files, normalized, formats):
            key, digest = store_stream(settings, stream, file_format)
            written.append(key)
            knowledge = Knowledge(title=Path(path).stem, content='', file_format=file_format, category=category,
                                  acl=build_grants(session, grants))
            session.add(knowledge); session.flush()
            task.items.append(ImportTaskItem(relative_path=path, storage_key=key, file_format=file_format,
                                             sha256=digest, knowledge_id=knowledge.id, operation='create'))
        session.flush()
    except Exception:
        for key in written:
            storage_path(settings, key).unlink(missing_ok=True)
        raise
    return task


def new_rebuild(session, settings, requester_id: int, knowledge: Knowledge, content: str | None = None,
                title: str | None = None, chunk_id: int | None = None) -> ImportTask:
    """保存显式编辑快照，重建期间不修改旧活动正文。"""
    task = ImportTask(requester_id=requester_id, category=knowledge.category, acl=[])
    session.add(task)
    replacement = knowledge.content if content is None else content
    if chunk_id is None:
        key, digest = store_text(settings, replacement)
    else:
        key, digest = None, ''
    task.items.append(ImportTaskItem(relative_path='知识-' + str(knowledge.id) + '-修订.txt',
        storage_key=key, sha256=digest, file_format='txt', knowledge_id=knowledge.id,
        expected_active_version_id=knowledge.active_version_id, operation='chunk' if chunk_id is not None else 'replace',
        replacement_content=replacement, replacement_title=title or knowledge.title,
        replacement_chunk_id=chunk_id))
    session.flush()
    return task


def retry_failed(task: ImportTask) -> None:
    """只重排失败子项，成功发布记录与尝试次数保持不变。"""
    failures = [item for item in task.items if item.status == 'failed']
    if not failures:
        raise HTTPException(409, '当前任务没有可重试的失败项')
    for item in failures:
        item.status, item.stage, item.progress, item.error = 'queued', 'queued', 0, None
        item.version_id = None
        item.attempt += 1
    refresh_task(task)


def dispatch_or_fail(session, task: ImportTask, enqueue) -> None:
    """任务先持久化，队列发送失败则明确落库失败并允许手动重试。"""
    task_id = task.id
    session.commit()
    try:
        enqueue(task_id)
    except Exception as error:
        session.refresh(task)
        for item in task.items:
            if item.status == 'queued':
                item.status, item.stage, item.error = 'failed', 'failed', '任务队列不可用，请修复连接后显式重试'
        refresh_task(task)
        session.commit()
        logger.error('流程=导入调度 状态=失败 task_id=%s error_type=%s', task_id, type(error).__name__)
        raise HTTPException(503, '导入队列不可用，任务已记录为失败，可在导入中心重试') from None
    logger.info('流程=导入调度 状态=成功 task_id=%s', task_id)
