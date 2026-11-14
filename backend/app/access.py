"""四维知识数据权限策略，任一有效授权命中允许读取。"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Principal:
    """保存数据库读取的当前用户身份。"""
    user_id: int
    department_id: int | None
    role_ids: frozenset[int]

@dataclass(frozen=True)
class Grant:
    """描述知识单位的一项授权。"""
    type: str
    entity_id: int | None = None

def can_read(principal: Principal, grants: list[Grant]) -> bool:
    """按直属部门、当前角色及本人执行OR，非法规则保持拒绝。"""
    for grant in grants:
        if grant.type == "global" and grant.entity_id is None:
            return True
        if grant.entity_id is None:
            continue
        if grant.type == "department" and grant.entity_id == principal.department_id:
            return True
        if grant.type == "role" and grant.entity_id in principal.role_ids:
            return True
        if grant.type == "user" and grant.entity_id == principal.user_id:
            return True
    return False
