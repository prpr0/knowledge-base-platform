"""验证真实数据库事务中的原子发布、过期构建和停用边界。"""
import pytest
from sqlalchemy import select
from backend.app.database import make_engine, session_factory
from backend.app.models import Base, Chunk, Knowledge, KnowledgeVersion
from backend.app.publishing import PublishError, publish_version

@pytest.fixture
def versions():
    """准备确定的持久化向量字段输入，仅测试发布一致性而不宣称模型推理。"""
    engine = make_engine('sqlite+pysqlite:///:memory:')
    Base.metadata.create_all(engine)
    with session_factory(engine)() as session:
        knowledge = Knowledge(title='旧标题', content='旧正文')
        session.add(knowledge); session.flush()
        old = KnowledgeVersion(knowledge_id=knowledge.id, number=1, title='旧标题', content='旧正文', file_format='txt', sha256='a'*64, source_path='old.txt', status='ready')
        session.add(old); session.flush(); knowledge.active_version_id=old.id
        candidate = KnowledgeVersion(knowledge_id=knowledge.id, number=2, title='新标题', content='新正文', file_format='md', sha256='b'*64, source_path='new.md', base_version_id=old.id)
        session.add(candidate); session.flush()
        # 测试向量只是发布函数的输入，不用于检索或模型效果证明。
        session.add(Chunk(version_id=candidate.id, sequence=0, content='新正文', section='新章节', page=None, location='new.md:1', token_count=3, embedding=[0.0]*1024, search_vector='新 正文'))
        session.commit()
        yield session, knowledge, old, candidate
    engine.dispose()


def test_publish_switches_all_current_fields(versions):
    """完成候选发布同时切换活动版本、标题、正文和格式，且可幂等重入。"""
    session, knowledge, old, candidate=versions
    result=publish_version(session,knowledge.id,candidate.id,old.id)
    session.commit()
    assert result.active_version_id==candidate.id
    assert (result.title,result.content,result.file_format)==('新标题','新正文','md')
    assert candidate.status=='ready'
    assert old.status=='ready'
    assert publish_version(session,knowledge.id,candidate.id,old.id).active_version_id==candidate.id


def test_incomplete_index_keeps_old_version(versions):
    """缺少真实索引字段的候选不能覆盖可用旧正文。"""
    session, knowledge, old, candidate=versions
    chunk=session.scalar(select(Chunk).where(Chunk.version_id==candidate.id))
    chunk.embedding=None; session.flush()
    with pytest.raises(PublishError): publish_version(session,knowledge.id,candidate.id,old.id)
    assert knowledge.active_version_id==old.id and knowledge.content=='旧正文'


def test_disabled_or_superseded_build_cannot_publish(versions):
    """停用或活动版本已变更时拒绝旧任务发布，不恢复过期正文。"""
    session,knowledge,old,candidate=versions
    knowledge.is_active=False; session.flush()
    with pytest.raises(PublishError): publish_version(session,knowledge.id,candidate.id,old.id)
    knowledge.is_active=True; session.flush()
    with pytest.raises(PublishError): publish_version(session,knowledge.id,candidate.id,None)
    assert knowledge.active_version_id==old.id
