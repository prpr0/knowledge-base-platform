"""为已认证用户提供私人会话、受控引用和管理员模型配置。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from .schemas import Input
from .models import Conversation, Message, User
from .services import require
from .logging_setup import request_id
from .chat import answer_stream, conversation_view, message_view, owned_conversation
from .evidence import authorized_sources
from .model_gateway import MODEL_NAME, configuration_view, save_configuration


class ConversationInput(Input):
    """用户只能编辑自己的会话标题。"""
    title: str = Field(default='新会话', min_length=1, max_length=200)


class QuestionInput(Input):
    """约束问题长度，不接受客户端证据或权限字段。"""
    question: str = Field(min_length=1, max_length=4000)


class ModelInput(Input):
    """服务凭证只写入，不在任何响应中返回。"""
    base_url: str = Field(min_length=1, max_length=512)
    model_name: str = MODEL_NAME
    api_key: str | None = Field(default=None, max_length=8192)
    release_manifest_path: str | None = None
    calibration_manifest_path: str | None = None


def build_chat_router(settings, database, current_user, sessions):
    """复用统一认证，流式请求用独立会话保证取消也能落审计。"""
    router = APIRouter()

    @router.get('/settings/model')
    def model_settings(actor: User = Depends(current_user), session=Depends(database)):
        """配置状态仅对有模型维护动作的用户可见。"""
        require(actor, 'model:manage')
        return configuration_view(session, settings)

    @router.put('/settings/model')
    def update_settings(payload: ModelInput, actor: User = Depends(current_user), session=Depends(database)):
        """验证真实发布后保存，尚无发布时保留明确未就绪状态。"""
        require(actor, 'model:manage')
        return save_configuration(session, settings, actor, payload.model_dump())

    @router.get('/conversations')
    def conversations(actor: User = Depends(current_user), session=Depends(database)):
        """列表仅查询当前账号的私人会话。"""
        require(actor, 'chat:ask')
        return [conversation_view(row) for row in session.scalars(select(Conversation).where(Conversation.user_id == actor.id).order_by(Conversation.updated_at.desc(), Conversation.id.desc()))]

    @router.post('/conversations')
    def create_conversation(payload: ConversationInput, actor: User = Depends(current_user), session=Depends(database)):
        """创建可编辑标题的新会话。"""
        require(actor, 'chat:ask')
        row = Conversation(user_id=actor.id, title=payload.title)
        session.add(row)
        session.commit()
        return conversation_view(row)

    @router.get('/conversations/{identifier}')
    def conversation(identifier: int, actor: User = Depends(current_user), session=Depends(database)):
        """他人会话不能从编号读取。"""
        return conversation_view(owned_conversation(session, actor, identifier))

    @router.patch('/conversations/{identifier}')
    def rename(identifier: int, payload: ConversationInput, actor: User = Depends(current_user), session=Depends(database)):
        """只修改本人会话标题。"""
        row = owned_conversation(session, actor, identifier, True)
        row.title = payload.title
        session.commit()
        return conversation_view(row)

    @router.delete('/conversations/{identifier}', status_code=204)
    def remove(identifier: int, actor: User = Depends(current_user), session=Depends(database)):
        """删除会话及其消息，审计外键置空保留计数。"""
        session.delete(owned_conversation(session, actor, identifier, True))
        session.commit()
        return Response(status_code=204)

    @router.get('/conversations/{identifier}/messages')
    def messages(identifier: int, actor: User = Depends(current_user), session=Depends(database)):
        """历史来源撤权或版本失效时抹去对应答案正文。"""
        owned_conversation(session, actor, identifier)
        return [message_view(session, actor, row) for row in session.scalars(select(Message).where(Message.conversation_id == identifier).order_by(Message.id))]

    @router.post('/conversations/{identifier}/ask')
    def ask(identifier: int, payload: QuestionInput, actor: User = Depends(current_user), session=Depends(database)):
        """在返回SSE前检查私人会话权限，事件内明确服务失败。"""
        owned_conversation(session, actor, identifier)
        require(actor, 'knowledge:read')
        return StreamingResponse(answer_stream(sessions, settings, actor.id, actor.token_version, identifier, payload.question, request_id.get()),
            media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @router.get('/citations/{message_id}/{label}')
    def citation(message_id: int, label: str, actor: User = Depends(current_user), session=Depends(database)):
        """引用预览重新校验整条答案的所有来源，不能只检查被点开的单项。"""
        row = session.get(Message, message_id)
        if row is None:
            raise HTTPException(404, '引用不存在')
        owned_conversation(session, actor, row.conversation_id)
        sources = authorized_sources(session, actor, row.citations)
        if row.status != 'complete' or not sources:
            raise HTTPException(403, '该答案的来源权限或版本已失效')
        source = next((ref for ref in sources if ref['label'] == label), None)
        if source is None:
            raise HTTPException(404, '引用编号不存在')
        return source

    return router
