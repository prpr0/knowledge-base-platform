"""候选版本发布的数据一致性边界。"""
from datetime import datetime, timezone
from sqlalchemy import select
from .models import Chunk, Knowledge, KnowledgeVersion
from .evidence import invalidate_faqs

class PublishError(ValueError):
    """表示候选未完成、已过期或知识已停用删除。"""

def publish_version(session, knowledge_id: int, version_id: int, expected_version_id: int | None):
    """仅将索引完整且未被后续编辑取代的候选原子发布。"""
    knowledge = session.get(Knowledge, knowledge_id, with_for_update=True)
    version = session.get(KnowledgeVersion, version_id, with_for_update=True)
    if knowledge is None or version is None or version.knowledge_id != knowledge_id:
        raise PublishError('知识或候选版本已删除')
    if not knowledge.is_active:
        raise PublishError('知识已停用，不能发布候选版本')
    if version.status == 'ready' and knowledge.active_version_id == version_id:
        return knowledge
    if version.status != 'building' or version.base_version_id != expected_version_id or knowledge.active_version_id != expected_version_id:
        raise PublishError('知识已被其他修改更新，请重新构建索引')
    chunks = list(session.scalars(select(Chunk).where(Chunk.version_id == version_id)))
    if not chunks or any(chunk.embedding is None or chunk.search_vector is None for chunk in chunks):
        raise PublishError('候选版本的向量或关键词索引尚未完整构建')
    invalidate_faqs(session, knowledge_id)
    version.status = 'ready'
    version.error = None
    knowledge.active_version_id = version.id
    knowledge.title = version.title
    knowledge.content = version.content
    knowledge.file_format = version.file_format
    knowledge.updated_at = datetime.now(timezone.utc)
    session.flush()
    return knowledge
