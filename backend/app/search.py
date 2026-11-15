"""使用真实BGE、PostgreSQL全文和pgvector执行鉴权后的混合检索。"""
from fastapi import HTTPException
from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import selectinload
from .models import Chunk, Knowledge, KnowledgeVersion, User
from .runtime_models import runtime_models
from .services import permissions, readable

RESTRICTED_MESSAGE = '部分参考资料因权限受限无法展示'


def refresh_identity(session, actor: User) -> User:
    """模型推理耗时后重新检查账号与当前角色，不信任旧身份快照。"""
    current = session.scalar(select(User).where(User.id == actor.id).options(selectinload(User.roles)).execution_options(populate_existing=True))
    if current is None or not current.is_active:
        raise HTTPException(401, '登录已失效，请重新登录')
    if 'knowledge:read' not in permissions(current):
        raise HTTPException(403, '没有知识读取权限')
    return current


def hybrid_search(session, settings, actor: User, query: str, limit: int = 8) -> dict:
    """各召回50条、RRF60、先ACL后真实重排，仅输出获准活动来源。"""
    import jieba
    actor = refresh_identity(session, actor)
    if session.bind.dialect.name != 'postgresql':
        raise RuntimeError('正式混合检索必须使用PostgreSQL与pgvector')
    runtime = runtime_models(settings)
    vector = runtime.embed([query])[0]
    base = select(Chunk, Knowledge).join(KnowledgeVersion, KnowledgeVersion.id == Chunk.version_id).join(Knowledge, Knowledge.id == KnowledgeVersion.knowledge_id).where(
        Knowledge.is_active.is_(True), Knowledge.active_version_id == KnowledgeVersion.id, KnowledgeVersion.status == 'ready')
    vector_rows = list(session.execute(base.order_by(Chunk.embedding.cosine_distance(vector), Chunk.id).limit(50)))
    segmented = ' '.join(word for word in jieba.cut(query) if word.strip())
    text_query = func.plainto_tsquery(literal_column("'simple'"), segmented)
    keyword_rows = list(session.execute(base.where(Chunk.search_vector.op('@@')(text_query)).order_by(
        func.ts_rank_cd(Chunk.search_vector, text_query).desc(), Chunk.id).limit(50)))
    fused = {}
    for rows in (vector_rows, keyword_rows):
        for rank, row in enumerate(rows, 1):
            chunk, knowledge = row
            if chunk.id not in fused:
                fused[chunk.id] = [chunk, knowledge, 0.0]
            fused[chunk.id][2] += 1.0 / (60 + rank)
    actor = refresh_identity(session, actor)
    allowed, restricted = [], False
    for chunk, knowledge, rrf_score in sorted(fused.values(), key=lambda item: (-item[2], item[0].id)):
        if readable(actor, knowledge):
            allowed.append((chunk, knowledge))
        else:
            restricted = True
    if not allowed:
        return {'items': [], 'restricted': restricted, 'message': RESTRICTED_MESSAGE + '。无法据可用资料作答' if restricted else '未检索到可用资料'}
    scores = runtime.rerank(query, [chunk.content for chunk, knowledge in allowed])
    actor = refresh_identity(session, actor)
    ranked = sorted(zip(allowed, scores), key=lambda item: (-item[1], item[0][0].id))
    remaining = max(0, 3072 - len(runtime.tokenizer.encode(query, add_special_tokens=False)))
    result = []
    for (chunk, knowledge), score in ranked:
        session.refresh(knowledge)
        if knowledge.active_version_id != chunk.version_id or not readable(actor, knowledge):
            restricted = True
            continue
        if chunk.token_count > remaining:
            continue
        result.append(dict(knowledge_id=knowledge.id, version_id=chunk.version_id, chunk_id=chunk.id,
            title=knowledge.title, section=chunk.section, page=chunk.page, location=chunk.location,
            content=chunk.content, score=score))
        remaining -= chunk.token_count
        if len(result) >= min(limit, 8):
            break
    return {'items': result, 'restricted': restricted, 'message': RESTRICTED_MESSAGE if restricted else ''}
