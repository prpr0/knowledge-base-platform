"""验证会话隔离、运营权限和未发布模型的可观察行为。"""
import pytest


def test_conversation_is_private_and_requires_permission(api):
    """会话只能由本人读取，未获问答动作的用户不能创建。"""
    client = api['client']
    denied = client.post('/api/conversations', json={}, headers=api['reader'])
    assert denied.status_code == 403
    created = client.post('/api/conversations', json={'title': '操作帮助'}, headers=api['admin'])
    assert created.status_code == 200, created.text
    identifier = created.json()['id']
    assert client.get(f'/api/conversations/{identifier}', headers=api['admin']).json()['title'] == '操作帮助'
    assert client.get(f'/api/conversations/{identifier}', headers=api['reader']).status_code in (403, 404)
    assert client.get('/api/conversations').status_code == 401


@pytest.mark.parametrize('path', ['/api/faq/candidates', '/api/gaps', '/api/metrics', '/api/audits', '/api/settings/model'])
def test_operations_enforce_separate_permissions(api, path):
    """每个运营入口显式鉴权，管理员空库取得真实空结果。"""
    assert api['client'].get(path, headers=api['reader']).status_code == 403
    result = api['client'].get(path, headers=api['admin'])
    assert result.status_code == 200, result.text
    if path == '/api/metrics':
        assert result.json()['pv'] == 0
        assert result.json()['uv'] == 0
    if path == '/api/settings/model':
        assert result.json()['release_verified'] is False
        assert 'api_key' not in result.json()
        assert 'api_key_ciphertext' not in result.json()

def make_source(api, publish_knowledge, title):
    """构造已发布资料，确定向量仅作为数据夹具而非模型验证。"""
    from sqlalchemy import select
    from backend.app.models import Chunk, Knowledge
    response = api['client'].post('/api/knowledge', headers=api['admin'], json=dict(title=title, content='公开操作依据。', acl=[{'type': 'global'}]))
    assert response.status_code in (200, 201), response.text
    identifier = response.json()['id']
    publish_knowledge(identifier)
    with api['app'].state.sessions() as session:
        row = session.get(Knowledge, identifier)
        chunk = session.scalar(select(Chunk).where(Chunk.version_id == row.active_version_id))
        return dict(knowledge_id=identifier, version_id=row.active_version_id, chunk_id=chunk.id, label='S1')


def test_history_and_citation_require_every_source(api, publish_knowledge):
    """撤回第二来源后，第一引用也不能单独读取或展示残缺历史答案。"""
    from backend.app.models import Conversation, Message, Role
    first = make_source(api, publish_knowledge, '通用操作')
    second = dict(make_source(api, publish_knowledge, '联合操作'), label='S2')
    with api['app'].state.sessions() as session:
        session.get(Role, api['ids']['reader_role']).permissions = ['knowledge:read', 'chat:ask']
        conversation = Conversation(user_id=api['ids']['reader'])
        session.add(conversation); session.flush()
        answer = Message(conversation_id=conversation.id, role='assistant', content='联合结论 [S1] [S2]', citations=[first, second])
        session.add(answer); session.commit()
        conversation_id, message_id = conversation.id, answer.id
    path = f'/api/conversations/{conversation_id}/messages'
    assert api['client'].get(path, headers=api['reader']).json()[0]['content']
    changed = api['client'].put('/api/knowledge/' + str(second['knowledge_id']) + '/acl', headers=api['admin'], json={'acl': [{'type': 'user', 'entity_id': api['ids']['admin']}]})
    assert changed.status_code == 200
    history = api['client'].get(path, headers=api['reader']).json()[0]
    assert history['status'] == 'unavailable' and history['content'] == '' and history['citations'] == []
    assert api['client'].get(f'/api/citations/{message_id}/S1', headers=api['reader']).status_code == 403


def test_faq_review_and_acl_invalidation(api, publish_knowledge):
    """人工发布持久化全部来源，ACL更新即禁用派生FAQ。"""
    from backend.app.models import FAQCandidate, FAQRelease
    from backend.app.operations import question_key
    source = make_source(api, publish_knowledge, '已审核资料')
    with api['app'].state.sessions() as session:
        candidate = FAQCandidate(question='怎样操作', question_key=question_key('怎样操作'), answer='按资料操作 [S1]', sources=[source], count=5, confidence=.95, cluster_key='a' * 64, created_by_id=api['ids']['admin'])
        session.add(candidate); session.commit(); identifier = candidate.id
    assert api['client'].get('/api/faq/releases', headers=api['admin']).json() == []
    invalid = api['client'].post(f'/api/faq/candidates/{identifier}/review', headers=api['admin'], json={'action': 'publish', 'answer': '伪造来源 [S9]'})
    assert invalid.status_code == 422
    published = api['client'].post(f'/api/faq/candidates/{identifier}/review', headers=api['admin'], json={'action': 'publish'})
    assert published.status_code == 200, published.text
    assert published.json()['enabled'] and len(published.json()['sources']) == 1
    response = api['client'].patch('/api/knowledge/' + str(source['knowledge_id']), headers=api['admin'], json={'is_active': False})
    assert response.status_code == 200
    with api['app'].state.sessions() as session:
        assert session.get(FAQRelease, published.json()['id']).enabled is False


def test_deleted_faq_source_remains_and_denies_whole_answer(api, publish_knowledge):
    """删除一个来源只置空外键，不能把两来源答案降成单来源继续通过。"""
    from backend.app.models import FAQRelease, FAQSource, User
    from backend.app.evidence import authorized_sources
    from backend.app.operations import question_key, source_refs
    first = make_source(api, publish_knowledge, '来源甲')
    second = dict(make_source(api, publish_knowledge, '来源乙'), label='S2')
    with api['app'].state.sessions() as session:
        release = FAQRelease(question='联合问题', question_key=question_key('联合问题'), answer='联合答案 [S1] [S2]', sources=[FAQSource(**first), FAQSource(**second)])
        session.add(release); session.commit(); identifier = release.id
    assert api['client'].delete('/api/knowledge/' + str(second['knowledge_id']), headers=api['admin']).status_code == 204
    with api['app'].state.sessions() as session:
        release = session.get(FAQRelease, identifier)
        assert len(release.sources) == 2
        assert any(source.knowledge_id is None for source in release.sources)
        assert authorized_sources(session, session.get(User, api['ids']['admin']), source_refs(release)) is None


def test_stream_failure_has_error_without_success_and_not_gap(api, publish_knowledge, monkeypatch):
    """用确定检索夹具隔离验证未发布模型失败协议，不冒充真实模型联调。"""
    import backend.app.chat as chat
    from backend.app.models import KnowledgeGap, QAAudit
    from sqlalchemy import select
    source = make_source(api, publish_knowledge, '可用依据')
    def retrieval(session, settings, actor, question, limit, audit):
        """只隔离上游检索，保留真实配置门禁与SSE落库。"""
        audit.update(candidates=[source['knowledge_id']], allowed=[source['knowledge_id']], blocked=[])
        return {'items': [dict(source, title='可用依据', content='操作依据', location='位置', score=.9)], 'restricted': False}
    monkeypatch.setattr(chat, 'hybrid_search', retrieval)
    conversation = api['client'].post('/api/conversations', headers=api['admin'], json={}).json()['id']
    response = api['client'].post(f'/api/conversations/{conversation}/ask', headers=api['admin'], json={'question': '怎样操作'})
    assert 'event: error' in response.text and 'event: done' not in response.text
    with api['app'].state.sessions() as session:
        audit = session.scalar(select(QAAudit))
        assert audit.status == 'error' and audit.model_called is False
        assert session.scalar(select(KnowledgeGap)) is None
    assert api['client'].get('/api/metrics', headers=api['admin']).json()['pv'] == 1


def test_permission_only_refusal_never_becomes_gap(api, monkeypatch):
    """纯权限阻断返回固定提示，模型消耗为零且不误记缺口。"""
    import backend.app.chat as chat
    from backend.app.models import KnowledgeGap
    from sqlalchemy import select
    def restricted(session, settings, actor, question, limit, audit):
        """隔离检索协议分支，不提供受限正文或标识给客户端。"""
        audit.update(candidates=[999], allowed=[], blocked=[999])
        return {'items': [], 'restricted': True}
    monkeypatch.setattr(chat, 'hybrid_search', restricted)
    conversation = api['client'].post('/api/conversations', headers=api['admin'], json={}).json()['id']
    result = api['client'].post(f'/api/conversations/{conversation}/ask', headers=api['admin'], json={'question': '受限问题'})
    assert 'event: done' in result.text and '999' not in result.text
    assert '部分参考资料因权限受限无法展示' in result.text
    with api['app'].state.sessions() as session:
        assert session.scalar(select(KnowledgeGap)) is None


def test_gap_task_is_idempotent_and_import_validates_owner(api):
    """重复转任务复用同一任务，真实multipart持久化任务关联。"""
    from backend.app.models import ImportTask, User
    from backend.app.operations import record_gap
    with api['app'].state.sessions() as session:
        gap = record_gap(session, session.get(User, api['ids']['admin']), '未知操作')
        session.commit(); identifier = gap.id
    path = f'/api/gaps/{identifier}/tasks'
    first = api['client'].post(path, headers=api['admin'], json={})
    assert first.status_code == 200, first.text
    assert api['client'].post(path, headers=api['admin'], json={}).json()['id'] == first.json()['id']
    imported = api['client'].post('/api/imports', headers=api['admin'], data={'paths': '["补充.txt"]', 'supplement_task_id': str(first.json()['id'])}, files=[('files', ('补充.txt', '补充依据'.encode(), 'text/plain'))])
    assert imported.status_code == 202, imported.text
    with api['app'].state.sessions() as session:
        assert session.get(ImportTask, imported.json()['id']).supplement_task_id == first.json()['id']


def test_metrics_distinguish_unknown_usage_and_scope(api):
    """有效失败计入PV，未知用量不伪造，其他用户问题不进当前文字排行。"""
    from backend.app.models import QAAudit
    with api['app'].state.sessions() as session:
        session.add_all([QAAudit(request_id='1' * 32, user_id=api['ids']['admin'], question='本人的问题', status='complete', source_mode='faq', model_called=False, latency_ms=10),
            QAAudit(request_id='2' * 32, user_id=api['ids']['reader'], question='其他用户的问题', status='error', source_mode='model', model_called=True, latency_ms=30)])
        session.commit()
    metrics = api['client'].get('/api/metrics', headers=api['admin']).json()
    assert metrics['pv'] == 2 and metrics['uv'] == 2 and metrics['token_unknown_requests'] == 1
    assert metrics['token_total'] == 0 and metrics['failures'] == 1
    assert metrics['latency']['p50_ms'] == 20
    assert all(row['question'] != '其他用户的问题' for row in metrics['top_questions'])

def test_null_department_does_not_share_gap_scope(api):
    """两个无部门账号提同一问题仍分别计数，空部门不是共享身份。"""
    from backend.app.models import User
    from backend.app.operations import record_gap
    with api['app'].state.sessions() as session:
        reader = session.get(User, api['ids']['reader'])
        reader.department_id = None
        first = record_gap(session, session.get(User, api['ids']['admin']), '相同未知问题')
        second = record_gap(session, reader, '相同未知问题')
        assert first.id != second.id

def test_mining_keeps_authorized_sources_with_unrelated_blocked_candidates(api, publish_knowledge, monkeypatch):
    """无关受限候选不能抹掉获准资料的高频问题，四次仍不产生推荐。"""
    from types import SimpleNamespace
    from backend.app.models import QAAudit, User
    from backend.app.maintenance import mine_candidates
    import backend.app.maintenance as maintenance
    source = make_source(api, publish_knowledge, '公开操作FAQ依据')
    def embedding(texts):
        """仅隔离确定的同问题聚类边界，不作为真实BGE证明。"""
        return [[1., 0.] for _ in texts]
    monkeypatch.setattr(maintenance, 'runtime_models', lambda settings: SimpleNamespace(embed=embedding))
    with api['app'].state.sessions() as session:
        actor = session.get(User, api['ids']['admin'])
        rows = [QAAudit(request_id=str(index).zfill(32), user_id=actor.id, question='怎样执行公开操作', sources=[source], blocked=[999], status='error', source_mode='pending', valid=True) for index in range(5)]
        assert mine_candidates(session, api['app'].state.settings, actor, rows[:4]) == 0
        assert mine_candidates(session, api['app'].state.settings, actor, rows) == 1

def test_prompt_budget_counts_token_ids_instead_of_batch_fields(api, publish_knowledge, monkeypatch):
    """聊天模板返回字典时，超预算问题仍必须拒绝，不能把两个字段当成两个token。"""
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend.app.models import User
    import backend.app.chat as chat
    source = make_source(api, publish_knowledge, '预算测试资料')
    def template(messages, **options):
        """仅确定性复现Transformers的BatchEncoding传输结构。"""
        count = sum(len(row['content']) for row in messages)
        return {'input_ids': [1] * count, 'attention_mask': [1] * count}
    monkeypatch.setattr(chat, 'runtime_models', lambda settings: SimpleNamespace(tokenizer=SimpleNamespace(apply_chat_template=template)))
    with api['app'].state.sessions() as session:
        actor = session.get(User, api['ids']['admin'])
        with pytest.raises(HTTPException) as failed:
            chat.prompt_messages(session, api['app'].state.settings, actor, 0, '问' * 4000, [source])
        assert failed.value.status_code == 422
