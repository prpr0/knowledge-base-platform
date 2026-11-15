"""逐次核对完整证据集合，统一控制历史、FAQ和引用的安全边界。"""
import re
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from .models import Chunk, Knowledge, KnowledgeVersion, User
from .services import readable


def authorized_sources(session, actor, refs):
    """全部来源仍处于当前活动版本且获准才返回正文，任何缺失都使整组失效。"""
    if not refs:
        return None
    expected_token_version = actor.token_version
    current = session.scalar(select(User).where(User.id == actor.id).options(selectinload(User.roles)).execution_options(populate_existing=True))
    if current is None or not current.is_active or current.token_version != expected_token_version:
        return None
    result, labels = [], set()
    for ref in refs:
        if not isinstance(ref, dict):
            return None
        label = ref.get('label')
        ids = [ref.get(key) for key in ('knowledge_id', 'version_id', 'chunk_id')]
        if not isinstance(label, str) or not re.fullmatch(r'S[1-9]\d*', label) or label in labels:
            return None
        if any(type(value) is not int or value <= 0 for value in ids):
            return None
        row = session.execute(select(Knowledge, KnowledgeVersion, Chunk).join(
            KnowledgeVersion, KnowledgeVersion.knowledge_id == Knowledge.id).join(
            Chunk, Chunk.version_id == KnowledgeVersion.id).where(
            Knowledge.id == ids[0], KnowledgeVersion.id == ids[1], Chunk.id == ids[2],
            Knowledge.active_version_id == KnowledgeVersion.id, KnowledgeVersion.status == 'ready'
        ).options(selectinload(Knowledge.acl)).execution_options(populate_existing=True)).first()
        if row is None or not readable(current, row[0]):
            return None
        knowledge, version, chunk = row
        labels.add(label)
        result.append(dict(label=label, knowledge_id=knowledge.id, version_id=version.id,
            chunk_id=chunk.id, title=version.title, section=chunk.section, page=chunk.page,
            location=chunk.location, content=chunk.content, acl_version=knowledge.acl_version))
    return result


def public_citations(refs):
    """只公开已鉴权的出处元数据，正文需经引用接口重新授权。"""
    keys = ('label', 'knowledge_id', 'version_id', 'chunk_id', 'title', 'section', 'page', 'location')
    return [{key: ref.get(key) for key in keys} for ref in refs]


def invalidate_faqs(session, knowledge_id):
    """在知识变更事务内禁用所有依赖该知识的FAQ，缓存命中仍须重新鉴权。"""
    from .models import FAQRelease, FAQSource
    identifiers = select(FAQSource.faq_id).where(FAQSource.knowledge_id == knowledge_id)
    session.execute(update(FAQRelease).where(FAQRelease.id.in_(identifiers)).values(enabled=False))
