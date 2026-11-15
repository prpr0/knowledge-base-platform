"""组织、登录与知识ACL的真实HTTP接口，管理权限和正文读取分别检查。"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Department, Knowledge, Role, User
from . import schemas as schema
from . import services as service
from .security import decode_token, hash_password, issue_token, verify_password
from .logging_setup import request_id
from .ingestion import dispatch_or_fail, new_rebuild
from .ingestion_routes import build_ingestion_router
from .evidence import invalidate_faqs
from .chat_routes import build_chat_router
from .operations_routes import build_operations_router
from .task_queue import enqueue_operations

logger = logging.getLogger(__name__)


def build_router(sessions, settings, enqueue, enqueue_ops=None) -> APIRouter:
    """为应用实例注入数据库与密钥，避免跨测试共享全局状态。"""
    router = APIRouter(prefix='/api')

    def database():
        """每个请求使用一个会话，异常时关闭并回滚事务。"""
        with sessions() as session:
            yield session

    def current_user(request: Request, session: Session = Depends(database)) -> User:
        """只相信签名身份，每次读取账号状态、角色与最新权限。"""
        scheme, _, token = request.headers.get('authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token:
            raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
        try:
            payload = decode_token(token, settings)
            user_id = int(payload['sub'])
            version = payload['ver']
            if user_id <= 0 or type(version) is not int:
                raise ValueError('非法身份')
        except (InvalidTokenError, ValueError, TypeError, KeyError):
            raise HTTPException(401, '登录已失效，请重新登录', headers={'WWW-Authenticate': 'Bearer'}) from None
        user = session.get(User, user_id)
        if not user or not user.is_active or user.token_version != version:
            raise HTTPException(401, '登录已失效，请重新登录', headers={'WWW-Authenticate': 'Bearer'})
        return user

    def commit(session: Session, actor: User, operation: str) -> None:
        """成功提交业务变更后记录安全标识，不记录正文或凭证。"""
        actor_id = actor.id
        session.commit()
        logger.info('流程=%s 状态=成功 request_id=%s user_id=%s', operation, request_id.get(), actor_id)

    @router.get('/health')
    def health(session: Session = Depends(database)):
        """实际检查数据库连接，不以进程存活冒充数据库可用。"""
        from sqlalchemy import text
        session.execute(text('SELECT 1'))
        return {'status': 'ok'}

    @router.post('/auth/login')
    def login(payload: schema.Login, session: Session = Depends(database)):
        """统一拒绝错误凭证，停用账号不能取得新令牌。"""
        user = session.scalar(select(User).where(User.username == payload.username))
        verified = verify_password(payload.password, user.password_hash if user else None)
        if not verified or not user or not user.is_active:
            logger.warning('流程=登录 状态=拒绝 request_id=%s', request_id.get())
            raise HTTPException(401, '账号或密码错误')
        logger.info('流程=登录 状态=成功 request_id=%s user_id=%s', request_id.get(), user.id)
        return {'access_token': issue_token(user.id, user.token_version, settings), 'token_type': 'bearer'}

    @router.get('/auth/me')
    def me(actor: User = Depends(current_user)):
        """输出当前数据库身份，不返回哈希与密钥。"""
        return service.user_view(actor)

    @router.get('/permissions')
    def permission_catalog(actor: User = Depends(current_user)):
        """返回已实现菜单与按钮权限，不提供通配符。"""
        service.require(actor, 'organization:read', 'role:manage')
        return schema.PERMISSIONS

    @router.get('/departments')
    def departments(actor: User = Depends(current_user), session: Session = Depends(database)):
        """读取组织树的扁平节点，由前端组成树。"""
        service.require(actor, 'organization:read', 'department:manage')
        return [service.department_view(item) for item in session.scalars(select(Department).order_by(Department.id))]

    @router.post('/departments', status_code=201)
    def create_department(payload: schema.DepartmentCreate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """建立部门并检查父节点，不允许悬空层级。"""
        service.require(actor, 'department:manage')
        service.validate_parent(session, None, payload.parent_id)
        item = Department(**payload.model_dump())
        session.add(item)
        commit(session, actor, '新增部门')
        return service.department_view(item)

    @router.patch('/departments/{record_id}')
    def update_department(record_id: int, payload: schema.DepartmentUpdate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """验证整个父链后变更部门，拒绝自父及间接循环。"""
        service.require(actor, 'department:manage')
        values = service.patch_values(payload, frozenset({'parent_id'}))
        service.validate_parent(session, record_id, values.get('parent_id'))
        item = service.get_record(session, Department, record_id)
        for key, value in values.items():
            setattr(item, key, value)
        commit(session, actor, '修改部门')
        return service.department_view(item)

    @router.delete('/departments/{record_id}', status_code=204)
    def delete_department(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """只删除没有子部门和直属用户的部门，同时清除其授权。"""
        service.require(actor, 'department:manage')
        service.validate_parent(session, None, None)
        item = service.get_record(session, Department, record_id)
        if session.scalar(select(Department.id).where(Department.parent_id == record_id)) or session.scalar(select(User.id).where(User.department_id == record_id)):
            raise HTTPException(409, '部门仍有子部门或用户')
        service.remove_target_grants(session, 'department', record_id)
        session.delete(item)
        commit(session, actor, '删除部门')
        return Response(status_code=204)

    @router.get('/roles')
    def roles(actor: User = Depends(current_user), session: Session = Depends(database)):
        """按组织读取或角色维护权限返回角色。"""
        service.require(actor, 'organization:read', 'role:manage')
        return [service.role_view(item) for item in session.scalars(select(Role).order_by(Role.id))]

    @router.post('/roles', status_code=201)
    def create_role(payload: schema.RoleCreate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """仅允许给角色分配已实现权限。"""
        service.require(actor, 'role:manage')
        item = Role(name=payload.name, permissions=service.valid_permissions(payload.permissions))
        session.add(item)
        commit(session, actor, '新增角色')
        return service.role_view(item)

    @router.patch('/roles/{record_id}')
    def update_role(record_id: int, payload: schema.RoleUpdate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """角色授权变更提升所有关联账号权限版本并保留管理员。"""
        users = service.lock_organization(session)
        service.require(actor, 'role:manage')
        item = service.get_record(session, Role, record_id, True)
        values = service.patch_values(payload)
        for key, value in values.items():
            setattr(item, key, service.valid_permissions(value) if key == 'permissions' else value)
        for user in users:
            if item in user.roles:
                user.permission_version += 1
        service.preserve_administrator(session)
        commit(session, actor, '修改角色')
        return service.role_view(item)

    @router.delete('/roles/{record_id}', status_code=204)
    def delete_role(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """移除角色关联和其知识授权，拒绝删除最后管理员能力。"""
        users = service.lock_organization(session)
        service.require(actor, 'role:manage')
        item = service.get_record(session, Role, record_id, True)
        for user in users:
            if item in user.roles:
                user.roles.remove(item)
                user.permission_version += 1
        service.preserve_administrator(session)
        service.remove_target_grants(session, 'role', record_id)
        session.delete(item)
        commit(session, actor, '删除角色')
        return Response(status_code=204)

    @router.get('/users')
    def users(actor: User = Depends(current_user), session: Session = Depends(database)):
        """输出组织可见账号，不返回任何密码信息。"""
        service.require(actor, 'organization:read', 'user:manage')
        return [service.user_view(item) for item in session.scalars(select(User).order_by(User.id))]

    @router.post('/users', status_code=201)
    def create_user(payload: schema.UserCreate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """创建带Argon2密码的账号，并解析真实部门和角色。"""
        service.require(actor, 'user:manage')
        service.check_department(session, payload.department_id)
        item = User(username=payload.username, display_name=payload.display_name,
                    password_hash=hash_password(payload.password), department_id=payload.department_id,
                    is_active=payload.is_active, roles=service.roles_for(session, payload.role_ids))
        session.add(item)
        commit(session, actor, '新增账号')
        return service.user_view(item)

    @router.patch('/users/{record_id}')
    def update_user(record_id: int, payload: schema.UserUpdate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """当前身份变更即时生效，密码或状态变化使旧令牌失效。"""
        service.lock_organization(session)
        service.require(actor, 'user:manage')
        item = service.get_record(session, User, record_id)
        values = service.patch_values(payload, frozenset({'department_id'}))
        if 'department_id' in values:
            service.check_department(session, values['department_id'])
        for key, value in values.items():
            if key == 'role_ids':
                item.roles = service.roles_for(session, value)
            elif key == 'password':
                item.password_hash = hash_password(value)
                item.token_version += 1
            else:
                if key == 'is_active' and item.is_active != value:
                    item.token_version += 1
                setattr(item, key, value)
        item.permission_version += 1
        service.preserve_administrator(session)
        commit(session, actor, '修改账号')
        return service.user_view(item)

    @router.delete('/users/{record_id}', status_code=204)
    def delete_user(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """删除用户、关联角色与个人授权，并保护最后管理员。"""
        service.lock_organization(session)
        service.require(actor, 'user:manage')
        item = service.get_record(session, User, record_id)
        service.remove_target_grants(session, 'user', record_id)
        session.delete(item)
        service.preserve_administrator(session)
        commit(session, actor, '删除账号')
        return Response(status_code=204)

    @router.get('/knowledge')
    def knowledge_list(actor: User = Depends(current_user), session: Session = Depends(database)):
        """只有当前有权正文才返回知识列表元数据。"""
        service.require(actor, 'knowledge:read')
        return [service.knowledge_view(item) for item in session.scalars(select(Knowledge).order_by(Knowledge.id)) if service.readable(actor, item)]

    @router.get('/knowledge/manage')
    def managed_knowledge(actor: User = Depends(current_user), session: Session = Depends(database)):
        """独立管理权限仅授予台账与ACL维护信息，不授予正文。"""
        service.require(actor, 'knowledge:manage')
        return [service.knowledge_view(item, True) for item in session.scalars(select(Knowledge).order_by(Knowledge.id))]

    @router.post('/knowledge', status_code=201)
    def create_knowledge(payload: schema.KnowledgeCreate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """创建文本候选并后台构建索引，缺省授权为空且不提前发布正文。"""
        service.require(actor, 'knowledge:manage')
        item = Knowledge(title=payload.title, content='', category=payload.category,
                         acl=service.build_grants(session, payload.acl))
        session.add(item)
        session.flush()
        task = new_rebuild(session, settings, actor.id, item, content=payload.content, title=payload.title)
        dispatch_or_fail(session, task, enqueue)
        return dict(service.knowledge_view(item, True), rebuild_task_id=task.id)

    @router.get('/knowledge/{record_id}')
    def read_knowledge(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """实时检查功能权限和四维ACL，管理员同样不能绕过。"""
        item = service.get_record(session, Knowledge, record_id)
        if not service.readable(actor, item):
            raise HTTPException(403, '无权读取该知识正文')
        return dict(service.knowledge_view(item), content=item.content)

    @router.patch('/knowledge/{record_id}')
    def update_knowledge(record_id: int, payload: schema.KnowledgeUpdate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """正文或标题编辑构建新版本，成功前保持旧活动正文可读。"""
        service.require(actor, 'knowledge:manage')
        item = service.get_record(session, Knowledge, record_id, True)
        values = service.patch_values(payload)
        for key in ('is_active', 'category'):
            if key in values:
                setattr(item, key, values[key])
        if values.get('is_active') is False:
            invalidate_faqs(session, item.id)
        if 'title' in values or 'content' in values:
            task = new_rebuild(session, settings, actor.id, item, content=values.get('content'), title=values.get('title'))
            dispatch_or_fail(session, task, enqueue)
            return dict(service.knowledge_view(item, True), rebuild_task_id=task.id)
        commit(session, actor, '修改知识状态或分类')
        return service.knowledge_view(item, True)

    @router.put('/knowledge/{record_id}/acl')
    def update_acl(record_id: int, payload: schema.ACLUpdate, actor: User = Depends(current_user), session: Session = Depends(database)):
        """完整替换四维授权并递增权限版本，下一请求即时生效。"""
        service.require(actor, 'knowledge:manage')
        item = service.get_record(session, Knowledge, record_id, True)
        item.acl = service.build_grants(session, payload.acl)
        item.acl_version += 1
        invalidate_faqs(session, item.id)
        commit(session, actor, '修改知识ACL')
        return service.knowledge_view(item, True)

    @router.delete('/knowledge/{record_id}', status_code=204)
    def delete_knowledge(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """事务删除知识及其授权，旧读取入口返回不存在。"""
        service.require(actor, 'knowledge:manage')
        item = service.get_record(session, Knowledge, record_id, True)
        invalidate_faqs(session, item.id)
        session.delete(item)
        commit(session, actor, '删除知识')
        return Response(status_code=204)

    router.include_router(build_ingestion_router(settings, database, current_user, enqueue))
    router.include_router(build_chat_router(settings, database, current_user, sessions))
    router.include_router(build_operations_router(settings, database, current_user, enqueue_ops or (lambda kind, identifier: enqueue_operations(settings, kind, identifier))))
    return router
