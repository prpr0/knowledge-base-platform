"""验证外部模型联调的配置、真实传输参数、引用与审计边界。"""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from backend.app.model_gateway import MODEL_NAME, configuration, save_configuration, verified_configuration
from backend.app.models import Message, ModelConfiguration, QAAudit, User
from test_qa_operations import make_source


def external_values(**changes):
    """生成不含真实凭证的外部模型配置输入。"""
    values = dict(mode='external_api', base_url='https://api.deepseek.com', model_name='deepseek-flash')
    values.update(changes)
    return values


def test_external_configuration_preserves_encrypted_key_and_formal_gate(api):
    """外部配置无需伪造发布，密钥加密且不回显，切回正式模式仍执行门禁。"""
    settings = replace(api['app'].state.settings, config_encryption_key=Fernet.generate_key().decode())
    secret = 'unit-test-external-key'
    with api['app'].state.sessions() as session:
        actor = session.get(User, api['ids']['admin'])
        view = save_configuration(session, settings, actor, external_values(api_key=secret))
        assert view['mode'] == 'external_api' and view['model_version'] == 'deepseek-flash'
        assert view['release_verified'] is False and view['calibration_verified'] is False
        assert secret not in json.dumps(view)
        row = session.get(ModelConfiguration, 1)
        assert row.api_key_ciphertext != secret and secret not in row.api_key_ciphertext
        save_configuration(session, settings, actor, external_values())
        config = verified_configuration(session, settings)
        assert config['api_key'] == secret and config['threshold'] is None
        assert config['model_name'] == 'deepseek-flash'
        save_configuration(session, settings, actor, dict(mode='finetuned', base_url='http://127.0.0.1:8000', model_name=MODEL_NAME))
        with pytest.raises(HTTPException) as failed:
            verified_configuration(session, settings)
        assert failed.value.status_code == 503


def test_first_save_preserves_environment_key_and_blank_update(api):
    """首次保存环境配置及后续空密钥更新不能丢失已有服务凭证。"""
    secret = 'unit-test-environment-key'
    settings = replace(api['app'].state.settings, model_mode='external_api', model_base_url='https://api.deepseek.com',
        model_name='deepseek-flash', model_api_key=secret, config_encryption_key=Fernet.generate_key().decode())
    with api['app'].state.sessions() as session:
        actor = session.get(User, api['ids']['admin'])
        for key in (None, ''):
            view = save_configuration(session, settings, actor, external_values(api_key=key))
            assert view['has_api_key'] is True and secret not in json.dumps(view)
            assert verified_configuration(session, settings)['api_key'] == secret


def test_external_mode_from_environment_and_database_precedence(api, monkeypatch):
    """运行环境允许显式联调模式，已有数据库配置仍优先且未知模式被拒绝。"""
    from backend.app.settings import Settings
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://unused:unused@127.0.0.1/unused')
    monkeypatch.setenv('JWT_SECRET', 'x' * 48)
    monkeypatch.setenv('MODEL_MODE', 'external_api')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('MODEL_NAME', 'deepseek-flash')
    settings = Settings.from_env()
    with api['app'].state.sessions() as session:
        assert configuration(session, settings)['mode'] == 'external_api'
        assert verified_configuration(session, settings)['threshold'] is None
        actor = session.get(User, api['ids']['admin'])
        save_configuration(session, settings, actor, dict(mode='finetuned', base_url='http://127.0.0.1:8000', model_name=MODEL_NAME))
        assert configuration(session, settings)['mode'] == 'finetuned'
    monkeypatch.setenv('MODEL_MODE', 'unknown')
    with pytest.raises(ValueError):
        Settings.from_env()


def test_model_settings_api_accepts_explicit_external_mode_only(api):
    """管理接口保存联调模式，缺省正式模式不能接入任意模型。"""
    client, headers = api['client'], api['admin']
    saved = client.put('/api/settings/model', headers=headers, json=external_values())
    assert saved.status_code == 200
    assert saved.json()['mode'] == 'external_api'
    assert saved.json()['has_api_key'] is False
    assert client.get('/api/settings/model', headers=headers).json()['mode'] == 'external_api'
    for payload in ({'base_url': 'https://api.deepseek.com', 'model_name': 'deepseek-flash'}, external_values(model_name='   ')):
        assert client.put('/api/settings/model', headers=headers, json=payload).status_code == 422
    assert client.put('/api/settings/model', headers=api['reader'], json=external_values()).status_code == 403


@pytest.mark.parametrize('disable_thinking', [False, True])
def test_stream_transport_uses_configured_model_and_real_usage(monkeypatch, disable_thinking):
    """OpenAI流请求使用选定模型，忽略推理片段并原样记录供应商用量。"""
    import backend.app.model_gateway as gateway
    actual_client = httpx.AsyncClient
    usage = dict(prompt_tokens=32, completion_tokens=8, total_tokens=40)
    def handle(request):
        """仅检查HTTP协议边界，不冒充真实上游联调。"""
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.deepseek.com/v1/chat/completions'
        assert body['model'] == 'deepseek-flash' and body['stream'] is True
        if disable_thinking:
            assert body['thinking'] == {'type': 'disabled'}
        else:
            assert 'thinking' not in body
        events = [dict(choices=[dict(delta=dict(reasoning_content='不可展示的推理'))]),
                  dict(choices=[dict(delta=dict(content='依据 [S1]'))]), dict(choices=[], usage=usage)]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(item) + '\n\n' for item in events) + 'data: [DONE]\n\n')
    def client(**options):
        """为传输回归注入隔离HTTP边界。"""
        return actual_client(transport=httpx.MockTransport(handle), **options)
    monkeypatch.setattr(gateway.httpx, 'AsyncClient', client)
    async def consume():
        """完整消费SSE事件并核对正文与真实用量。"""
        return [item async for item in gateway.stream_completion(dict(base_url='https://api.deepseek.com', model_name='deepseek-flash', api_key='', disable_thinking=disable_thinking), [])]
    assert asyncio.run(consume()) == [('delta', '依据 [S1]'), ('usage', usage)]


@pytest.mark.parametrize('reply,success', [('操作依据 [S1]', True), ('未知引用 [S99]', False)])
def test_external_answer_keeps_citations_history_and_audit(api, publish_knowledge, monkeypatch, reply, success):
    """外部模型沿用引用验证与失败清理，成功答案和审计明确记录联调来源。"""
    import backend.app.chat as chat
    source = make_source(api, publish_knowledge, '外部API联调依据')
    def retrieval(session, settings, actor, question, limit, audit):
        """只隔离检索计算，保留真实来源鉴权、配置、落库和事件。"""
        audit.update(candidates=[source['knowledge_id']], allowed=[source['knowledge_id']], blocked=[])
        return {'items': [dict(source, title='外部API联调依据', content='操作依据', location='位置', score=-2.0)], 'restricted': False}
    def template(messages, **options):
        """用确定预算隔离tokenizer计算。"""
        return {'input_ids': [1] * 100}
    async def completion(config, messages):
        """隔离上游计算并检查获准证据确实进入提示。"""
        assert config['model_name'] == 'deepseek-flash' and config['threshold'] is None
        assert '操作依据' in messages[-1]['content']
        yield 'delta', reply
        yield 'usage', dict(prompt_tokens=100, completion_tokens=10, total_tokens=110)
    monkeypatch.setattr(chat, 'hybrid_search', retrieval)
    monkeypatch.setattr(chat, 'runtime_models', lambda settings: SimpleNamespace(tokenizer=SimpleNamespace(apply_chat_template=template)))
    monkeypatch.setattr(chat, 'stream_completion', completion)
    saved = api['client'].put('/api/settings/model', headers=api['admin'], json=external_values())
    assert saved.status_code == 200
    identifier = api['client'].post('/api/conversations', headers=api['admin'], json={}).json()['id']
    response = api['client'].post(f'/api/conversations/{identifier}/ask', headers=api['admin'], json={'question': '怎样操作'})
    assert ('event: done' in response.text) is success
    assert ('event: error' in response.text) is not success
    with api['app'].state.sessions() as session:
        audit = session.scalar(select(QAAudit))
        answer = session.scalar(select(Message).where(Message.role == 'assistant'))
        assert audit.model_called is True and audit.source_mode == 'external_api'
        assert audit.model_version == 'deepseek-flash'
        assert audit.status == ('complete' if success else 'error')
        assert answer.content == (reply if success else '')
    if success:
        messages = api['client'].get(f'/api/conversations/{identifier}/messages', headers=api['admin']).json()
        assert messages[-1]['source_mode'] == 'external_api' and messages[-1]['model_version'] == 'deepseek-flash'
        citation = api['client'].get(f"/api/citations/{messages[-1]['id']}/S1", headers=api['admin'])
        assert citation.status_code == 200 and citation.json()['content']
