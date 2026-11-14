"""集中实现组织一致性、当前权限与知识授权的数据操作。"""
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from .access import Grant, Principal, can_read
from .models import Department, Knowledge, KnowledgeACL, Role, User
from .schemas import PERMISSION_CODES


def permissions(user: User) -> set[str]:
    """从当前数据库角色计算有效权限，不识别管理员特例。"""
    return {code for role in user.roles for code in role.permissions if code in PERMISSION_CODES}


def require(user: User, *codes: str) -> None:
    """操作需要至少一个明确授予的功能权限。"""
    if not permissions(user).intersection(codes):
        raise HTTPException(403, "没有该操作权限")


def get_record(session: Session, model, record_id: int, lock: bool = False):
    """读取存在的实体，必要时锁定以串行化修改。"""
    item = session.get(model, record_id, with_for_update=lock)
    if item is None:
        raise HTTPException(404, "记录不存在")
    return item


def user_view(user: User) -> dict:
    """返回不包含密码与哈希的当前账号信息。"""
    return dict(id=user.id, username=user.username, display_name=user.display_name,
                department_id=user.department_id, role_ids=sorted(role.id for role in user.roles),
                is_active=user.is_active, permission_version=user.permission_version,
                permissions=sorted(permissions(user)))


def role_view(role: Role) -> dict:
    """输出角色与已配置权限码。"""
    return dict(id=role.id, name=role.name, permissions=role.permissions)


def department_view(department: Department) -> dict:
    """输出部门树节点的直接父关系。"""
    return dict(id=department.id, name=department.name, parent_id=department.parent_id)


def knowledge_view(item: Knowledge, include_acl: bool = False) -> dict:
    """返回管理或列表元数据，绝不隐式附带正文。"""
    result = dict(id=item.id, title=item.title, acl_version=item.acl_version)
    if include_acl:
        result['acl'] = [dict(type=grant.type, entity_id=grant.entity_id) for grant in item.acl]
    return result


def readable(user: User, knowledge: Knowledge) -> bool:
    """正文读取同时受功能权限及四维当前身份约束。"""
    principal = Principal(user.id, user.department_id, frozenset(role.id for role in user.roles))
    return 'knowledge:read' in permissions(user) and can_read(
        principal, [Grant(grant.type, grant.entity_id) for grant in knowledge.acl])


def patch_values(payload, nullable: frozenset[str] = frozenset()) -> dict:
    """区分缺省与显式null，拒绝破坏必填字段的补丁。"""
    values = payload.model_dump(exclude_unset=True)
    if any(value is None and key not in nullable for key, value in values.items()):
        raise HTTPException(422, "必填字段不能设为null")
    return values


def valid_permissions(codes: list[str]) -> list[str]:
    """拒绝不存在的权限码而非保留通配或未知动作。"""
    if not set(codes) <= PERMISSION_CODES:
        raise HTTPException(422, "包含未实现的权限码")
    return sorted(set(codes))


def roles_for(session: Session, ids: list[int]) -> list[Role]:
    """解析存在的角色，去重并拒绝无效引用。"""
    unique_ids = set(ids)
    roles = list(session.scalars(select(Role).where(Role.id.in_(unique_ids)))) if ids else []
    if len(roles) != len(unique_ids):
        raise HTTPException(422, "角色不存在")
    return roles


def check_department(session: Session, department_id: int | None) -> None:
    """账号只能关联存在的直属部门。"""
    if department_id is not None and session.get(Department, department_id) is None:
        raise HTTPException(422, "部门不存在")


def lock_organization(session: Session) -> list[User]:
    """按固定顺序锁定账号，使管理员保留检查覆盖并发角色变更。"""
    return list(session.scalars(select(User).order_by(User.id).with_for_update()
                               .execution_options(populate_existing=True)))


def preserve_administrator(session: Session) -> None:
    """确保始终保留一个可维护账号与角色的有效用户。"""
    session.flush()
    users = session.scalars(select(User).execution_options(populate_existing=True))
    if not any(user.is_active and {'user:manage', 'role:manage'} <= permissions(user) for user in users):
        raise HTTPException(409, "必须保留至少一个可维护用户和角色的有效管理员")


def validate_parent(session: Session, record_id: int | None, parent_id: int | None) -> None:
    """锁定部门树并拒绝自父及间接循环。"""
    nodes = {node.id: node for node in session.scalars(select(Department).order_by(Department.id).with_for_update())}
    seen = {record_id} if record_id is not None else set()
    current = parent_id
    while current is not None:
        if current in seen:
            raise HTTPException(409, "部门不能形成循环")
        seen.add(current)
        if current not in nodes:
            raise HTTPException(422, "父部门不存在")
        current = nodes[current].parent_id


def build_grants(session: Session, grants) -> list[KnowledgeACL]:
    """逐项校验授权目标并锁定目标，避免删除后授权编号复活。"""
    targets = {'department': Department, 'role': Role, 'user': User}
    result, seen = [], set()
    for grant in grants:
        key = (grant.type, grant.entity_id)
        if key in seen:
            continue
        seen.add(key)
        if grant.type != 'global' and session.get(targets[grant.type], grant.entity_id, with_for_update=True) is None:
            raise HTTPException(422, "授权目标不存在")
        result.append(KnowledgeACL(type=grant.type, entity_id=grant.entity_id))
    return result


def remove_target_grants(session: Session, target_type: str, target_id: int) -> None:
    """删除被移除实体的授权并提升相关知识权限版本。"""
    grants = list(session.scalars(select(KnowledgeACL).where(KnowledgeACL.type == target_type,
                                                           KnowledgeACL.entity_id == target_id)))
    for knowledge_id in {grant.knowledge_id for grant in grants}:
        item = session.get(Knowledge, knowledge_id, with_for_update=True)
        item.acl_version += 1
    session.execute(delete(KnowledgeACL).where(KnowledgeACL.type == target_type,
                                              KnowledgeACL.entity_id == target_id))
