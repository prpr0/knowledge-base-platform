"""执行真实解析、生成token切片、BGE嵌入和原子版本发布。"""
from pathlib import Path
import logging
import traceback
from sqlalchemy import func, literal_column, select
from .database import make_engine, session_factory
from .documents import Block, DocumentError, ParsedDocument, parse_document, split_document
from .ingestion import refresh_task, storage_path, store_text
from .models import Chunk, ImportTask, ImportTaskItem, Knowledge, KnowledgeVersion
from .publishing import PublishError, publish_version
from .runtime_models import runtime_models

logger = logging.getLogger(__name__)


def _claim(sessions, item_id: str):
    """锁定尚未处理子项，重复投递不再次生成成功版本。"""
    with sessions() as session:
        item = session.get(ImportTaskItem, item_id, with_for_update=True)
        if item is None or item.status != 'queued':
            return None
        item.status, item.stage, item.progress, item.error = 'running', 'parsing', 5, None
        refresh_task(item.task)
        data = {key: getattr(item, key) for key in ('id', 'task_id', 'relative_path', 'storage_key', 'file_format', 'sha256',
            'knowledge_id', 'expected_active_version_id', 'operation', 'replacement_content', 'replacement_title', 'replacement_chunk_id')}
        session.commit()
        return data


def _document(sessions, settings, data):
    """从原文件或明确修订的版本快照形成可追溯正文。"""
    if data['operation'] != 'chunk':
        document = parse_document(storage_path(settings, data['storage_key']), data['relative_path'])
        if data['replacement_title']:
            document = ParsedDocument(data['replacement_title'], document.blocks, document.format)
        return document
    with sessions() as session:
        current = session.get(Knowledge, data['knowledge_id'])
        if current is None or current.active_version_id != data['expected_active_version_id']:
            raise PublishError('知识已被更新或删除，请重新选择切片编辑')
        chunks = list(session.scalars(select(Chunk).where(Chunk.version_id == current.active_version_id).order_by(Chunk.sequence)))
        if not any(chunk.id == data['replacement_chunk_id'] for chunk in chunks):
            raise DocumentError('要编辑的切片不属于当前活动版本')
        blocks = [Block(data['replacement_content'] if chunk.id == data['replacement_chunk_id'] else chunk.content,
                        section=chunk.section, page=chunk.page,
                        location=chunk.location + ('（人工修订）' if chunk.id == data['replacement_chunk_id'] else '')) for chunk in chunks]
        document = ParsedDocument(data['replacement_title'] or current.title, blocks, 'txt')
    # 修订是有序切片快照组成的独立文本源，保留原出处与重叠上下文，不覆盖旧原文件。
    key, digest = store_text(settings, '\n\n'.join(block.text for block in document.blocks))
    data['storage_key'], data['sha256'], data['file_format'] = key, digest, 'txt'
    return document


def _begin_version(sessions, data, document) -> int:
    """按知识单位锁分配候选版本号，不提前替换活动版本。"""
    if not document.blocks or not any(block.text.strip() for block in document.blocks):
        raise DocumentError('文档没有可提取正文')
    if len(document.title) > 255:
        raise DocumentError('文档标题超过255字符，请调整原文标题')
    with sessions() as session:
        knowledge = session.get(Knowledge, data['knowledge_id'], with_for_update=True)
        if knowledge is None:
            raise PublishError('知识已删除，不能恢复导入')
        if knowledge.active_version_id != data['expected_active_version_id']:
            raise PublishError('知识已被其他修改更新，请重新提交')
        number = (session.scalar(select(func.max(KnowledgeVersion.number)).where(KnowledgeVersion.knowledge_id == knowledge.id)) or 0) + 1
        version = KnowledgeVersion(knowledge_id=knowledge.id, number=number, title=document.title,
            content='\n\n'.join(block.text for block in document.blocks), file_format=document.format,
            storage_key=data['storage_key'], sha256=data['sha256'], source_path=data['relative_path'],
            base_version_id=data['expected_active_version_id'], status='building')
        session.add(version); session.flush()
        item = session.get(ImportTaskItem, data['id'])
        item.version_id, item.stage, item.progress = version.id, 'chunking', 20
        item.storage_key, item.sha256 = data['storage_key'], data['sha256']
        version_id = version.id
        session.commit()
        return version_id


def _index(sessions, settings, data, version_id: int, drafts) -> None:
    """分批真实嵌入并写入候选向量与分词全文索引。"""
    import jieba
    runtime = runtime_models(settings)
    for offset in range(0, len(drafts), 8):
        batch = drafts[offset:offset + 8]
        embeddings = runtime.embed([draft.text for draft in batch])
        with sessions() as session:
            item = session.get(ImportTaskItem, data['id'], with_for_update=True)
            if item is None or item.knowledge_id is None:
                raise PublishError('知识已删除，终止索引构建')
            for sequence, draft, embedding in zip(range(offset, offset + len(batch)), batch, embeddings):
                words = ' '.join(token for token in jieba.cut(draft.text) if token.strip())
                search_vector = func.to_tsvector(literal_column("'simple'"), words) if session.bind.dialect.name == 'postgresql' else words
                session.add(Chunk(version_id=version_id, sequence=sequence, content=draft.text, section=draft.section,
                    page=draft.page, location=draft.location, token_count=draft.token_count,
                    embedding=embedding, search_vector=search_vector))
            item.stage = 'embedding'
            item.progress = 25 + round(65 * (offset + len(batch)) / len(drafts))
            session.commit()


def _fail(sessions, item_id: str, error: Exception) -> None:
    """失败只废弃候选，旧活动版本保持可用，错误不含凭证。"""
    message = str(error) if isinstance(error, (DocumentError, PublishError)) else '解析或索引构建失败（' + type(error).__name__ + '），请检查本地模型与服务日志'
    with sessions() as session:
        item = session.get(ImportTaskItem, item_id, with_for_update=True)
        if item is None:
            return
        item.status, item.stage, item.error = 'failed', 'failed', message[:1000]
        if item.version_id:
            version = session.get(KnowledgeVersion, item.version_id)
            if version and version.status != 'ready':
                version.status, version.error = 'failed', message[:1000]
        refresh_task(item.task)
        session.commit()
    stack = ''.join(traceback.format_list(traceback.extract_tb(error.__traceback__)))
    logger.error('流程=导入 状态=失败 task_item_id=%s error_type=%s stack=%s', item_id, type(error).__name__, stack)


def process_task(settings, task_id: str) -> None:
    """从PG领取queued子项，逐项完成完整导入，不自动重试失败项。"""
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    try:
        with sessions() as session:
            task = session.get(ImportTask, task_id)
            if task is None:
                return
            pending = [item.id for item in task.items if item.status == 'queued']
        for item_id in pending:
            data = _claim(sessions, item_id)
            if data is None:
                continue
            logger.info('流程=导入 步骤=开始 task_id=%s task_item_id=%s', task_id, item_id)
            try:
                document = _document(sessions, settings, data)
                version_id = _begin_version(sessions, data, document)
                drafts = split_document(document, runtime_models(settings).tokenizer)
                if not drafts:
                    raise DocumentError('文档无法形成有效切片')
                _index(sessions, settings, data, version_id, drafts)
                with sessions() as session:
                    item = session.get(ImportTaskItem, item_id, with_for_update=True)
                    if item is None or item.knowledge_id is None:
                        raise PublishError('知识已删除，不能发布')
                    item.stage, item.progress = 'publishing', 95
                    publish_version(session, item.knowledge_id, version_id, data['expected_active_version_id'])
                    item.status, item.stage, item.progress, item.error = 'succeeded', 'done', 100, None
                    refresh_task(item.task)
                    session.commit()
                logger.info('流程=导入 状态=成功 task_id=%s task_item_id=%s version_id=%s', task_id, item_id, version_id)
            except Exception as error:
                _fail(sessions, item_id, error)
    finally:
        engine.dispose()
