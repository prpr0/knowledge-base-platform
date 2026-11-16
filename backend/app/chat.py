"""编排私人问答、真实模型流、历史撤权和逐轮业务审计。"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select
from .models import Conversation, Message, QAAudit, User
from .evidence import authorized_sources, public_citations
from .model_gateway import SYSTEM_PROMPT, stream_completion, verified_configuration
from .operations import find_faq, record_gap
from .runtime_models import runtime_models
from .search import hybrid_search, RESTRICTED_MESSAGE
from .services import require

logger = logging.getLogger(__name__)


def owned_conversation(session, actor, identifier, lock=False):
    """不存在与他人会话统一返回404，避免枚举私人记录。"""
    require(actor, 'chat:ask')
    row = session.scalar(select(Conversation).where(Conversation.id == identifier, Conversation.user_id == actor.id).with_for_update() if lock else
        select(Conversation).where(Conversation.id == identifier, Conversation.user_id == actor.id))
    if row is None:
        raise HTTPException(404, '会话不存在')
    return row


def conversation_view(row):
    """只返回当前用户会话的元数据。"""
    return dict(id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at)


def message_view(session, actor, message):
    """每次读取完整复核来源，失效答案不返回旧正文。"""
    refs = message.citations or []
    sources = authorized_sources(session, actor, refs) if refs else []
    available = message.status == 'complete' and (not refs or sources is not None)
    return dict(id=message.id, role=message.role, content=message.content if available else '',
        status=message.status if available else 'unavailable', citations=public_citations(sources or []) if available else [],
        model_version=message.model_version, source_mode=message.source_mode, created_at=message.created_at)


def event(name, value):
    """生成标准单行JSON SSE数据，正文换行留在JSON字符串中。"""
    return 'event: ' + name + '\ndata: ' + json.dumps(value, ensure_ascii=False) + '\n\n'


def prompt_messages(session, settings, actor, conversation_id, question, sources):
    """完整按聊天模板计数，在4096预算内保留本轮证据和可核验历史。"""
    tokenizer = runtime_models(settings).tokenizer
    selected = authorized_sources(session, actor, list(sources[:8]))
    if not selected:
        raise HTTPException(403, '证据权限或版本已变化')
    history = []
    rows = list(session.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.status == 'complete').order_by(Message.id.desc()).limit(12)))[::-1]
    current_refs = {(ref['knowledge_id'], ref['version_id'], ref['chunk_id']): ref['label'] for ref in selected}
    for index in range(0, len(rows) - 1):
        user, answer = rows[index:index + 2]
        if user.role != 'user' or answer.role != 'assistant' or not answer.citations:
            continue
        valid = authorized_sources(session, actor, answer.citations)
        if not valid or any((ref['knowledge_id'], ref['version_id'], ref['chunk_id']) not in current_refs for ref in valid):
            continue
        mapping = {ref['label']: current_refs[(ref['knowledge_id'], ref['version_id'], ref['chunk_id'])] for ref in valid}
        text = re.sub(r'\[(S\d+)\]', lambda match: '[' + mapping.get(match[1], match[1]) + ']', answer.content)
        history.extend([dict(role='user', content=user.content), dict(role='assistant', content=text)])
    while selected:
        evidence = '\n\n'.join(f"[{ref['label']}] 文档：{ref['title']}；版本：{ref['version_id']}；出处：{ref['location']}\n{ref['content']}" for ref in selected)
        messages = [dict(role='system', content=SYSTEM_PROMPT), *history, dict(role='user', content=f'问题：{question}\n\n编号证据：\n{evidence}')]
        encoded = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=True)
        count = len(encoded['input_ids'])
        if count <= 3072:
            return messages, selected
        if history:
            history = history[2:]
        else:
            selected.pop()
    raise HTTPException(422, '问题与完整证据超出上下文预算，请缩短问题')


async def answer_stream(sessions, settings, actor_id, token_version, conversation_id, question, request_identifier):
    """流独立持有会话；终态只在完成校验与落库后发送，取消保留独立审计。"""
    started = time.perf_counter()
    identifier = request_identifier or uuid4().hex
    audit_id = identifier
    answer_id = None
    terminal = 'error'
    text, refs, usage = '', [], None
    mode, version, error_code = 'error', None, None
    try:
        with sessions() as session:
            actor = session.get(User, actor_id)
            if actor is None or not actor.is_active or actor.token_version != token_version:
                raise HTTPException(401, '登录已失效')
            conversation = owned_conversation(session, actor, conversation_id)
            existing = session.get(QAAudit, identifier)
            if existing is not None:
                raise HTTPException(409, '请求已经处理')
            audit = QAAudit(request_id=identifier, conversation_id=conversation_id, user_id=actor.id, department_id=actor.department_id,
                question=question, status='running', source_mode='pending')
            session.add(audit)
            question_row = Message(conversation_id=conversation_id, role='user', content=question)
            answer = Message(conversation_id=conversation_id, role='assistant', status='running', request_id=identifier)
            session.add_all([question_row, answer])
            session.flush()
            answer_id = answer.id
            audit.message_id = answer.id
            conversation.updated_at = datetime.now(timezone.utc)
            session.commit()
            logger.info('流程=问答 步骤=开始 request_id=%s user_id=%s', identifier, actor_id)
            faq = await asyncio.to_thread(find_faq, session, settings, actor, question)
            restricted = False
            if faq:
                mode, text, refs = 'faq', faq['answer'], faq['sources']
                audit.faq_release_id = faq['release_id']
                audit.allowed = sorted({ref['knowledge_id'] for ref in refs})
            else:
                decisions = {}
                search = await asyncio.to_thread(hybrid_search, session, settings, actor, question, 8, decisions)
                restricted = search['restricted']
                audit.candidates, audit.allowed, audit.blocked = [decisions.get(key, []) for key in ('candidates', 'allowed', 'blocked')]
                refs = [dict(ref, label=f'S{index}') for index, ref in enumerate(search['items'], 1)]
                audit.sources = public_citations(refs)
                session.commit()
                if not refs:
                    mode, text = 'refusal', RESTRICTED_MESSAGE + '。无法据可用资料作答' if restricted else '未检索到可用资料，请补充相关文档或问题条件。'
                    if not restricted:
                        record_gap(session, actor, question)
                else:
                    config = await asyncio.to_thread(verified_configuration, session, settings)
                    if config['threshold'] is not None and max(ref['score'] for ref in refs) < config['threshold']:
                        mode, text = 'refusal', '现有证据不足以支持可靠回答，请补充相关资料或适用条件。'
                        record_gap(session, actor, question, max(ref['score'] for ref in refs))
                        refs = []
                    else:
                        messages, refs = await asyncio.to_thread(prompt_messages, session, settings, actor, conversation_id, question, refs)
                        mode, version = 'external_api' if config['mode'] == 'external_api' else 'model', config['model_version']
                        audit.model_called, audit.model_version = True, version
                        audit.sources = public_citations(refs)
                        audit.source_mode = mode
                        session.commit()
                        yield event('meta', dict(request_id=identifier, restricted=restricted, model_version=version, source_mode=mode, message_id=answer_id))
                        async for kind, value in stream_completion(config, messages):
                            current = authorized_sources(session, actor, refs)
                            if not current or actor.token_version != token_version:
                                raise HTTPException(403, '资料权限或版本已变化，本轮回答已中止')
                            require(actor, 'chat:ask')
                            if kind == 'delta':
                                text += value
                                yield event('delta', dict(text=value))
                            else:
                                usage = value
                        cited = set(re.findall(r'\[(S\d+)\]', text))
                        labels = {ref['label'] for ref in refs}
                        if not text.strip() or not cited or not cited.issubset(labels):
                            raise HTTPException(502, '模型回答缺少有效引用或包含未知引用编号')
            if mode not in ('model', 'external_api'):
                yield event('meta', dict(request_id=identifier, restricted=restricted, model_version=None, source_mode=mode, message_id=answer_id))
                if refs and not authorized_sources(session, actor, refs):
                    raise HTTPException(403, '来源已失效')
                usage = dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
                yield event('delta', dict(text=text))
            if refs and not authorized_sources(session, actor, refs):
                raise HTTPException(403, '来源已失效')
            session.refresh(actor)
            if not actor.is_active or actor.token_version != token_version:
                raise HTTPException(401, '登录已失效')
            session.expire(actor, ['roles'])
            require(actor, 'chat:ask')
            terminal = 'complete'
            audit.sources, audit.source_mode, audit.status = public_citations(refs), mode, terminal
            audit.token_usage, audit.latency_ms = usage, (time.perf_counter() - started) * 1000
            answer.content, answer.citations, answer.status = text, public_citations(refs), terminal
            answer.model_version, answer.source_mode = version, mode
            session.commit()
            yield event('citations', dict(items=public_citations(refs)))
            yield event('usage', dict(tokens=usage, known=usage is not None, latency_ms=audit.latency_ms))
            yield event('done', dict(message_id=answer_id, status=terminal))
            logger.info('流程=问答 状态=成功 request_id=%s source_mode=%s', identifier, mode)
    except (asyncio.CancelledError, GeneratorExit):
        terminal, error_code = 'cancelled', 'client_cancelled'
        raise
    except Exception as exc:
        error_code = 'model_or_service_unavailable' if not isinstance(exc, HTTPException) else str(exc.status_code)
        logger.error('流程=问答 状态=失败 request_id=%s error_type=%s', identifier, type(exc).__name__)
        detail = exc.detail if isinstance(exc, HTTPException) else '问答服务处理失败，请凭request_id检查后台日志'
        yield event('error', dict(request_id=identifier, detail=detail, code=error_code))
    finally:
        if terminal != 'complete':
            with sessions() as session:
                audit = session.get(QAAudit, audit_id)
                if audit:
                    audit.status, audit.error_code = terminal, error_code
                    audit.token_usage, audit.latency_ms = usage, (time.perf_counter() - started) * 1000
                    answer = session.get(Message, answer_id) if answer_id else None
                    if answer:
                        answer.content, answer.citations, answer.status = '', [], terminal
                    session.commit()
