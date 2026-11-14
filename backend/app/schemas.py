"""定义组织管理及知识授权的严格输入结构。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

PERMISSIONS = [
    {"code": "organization:read", "label": "组织管理", "kind": "menu", "parent": None},
    {"code": "department:manage", "label": "维护部门", "kind": "button", "parent": "organization:read"},
    {"code": "user:manage", "label": "维护用户", "kind": "button", "parent": "organization:read"},
    {"code": "role:manage", "label": "维护角色权限", "kind": "button", "parent": "organization:read"},
    {"code": "knowledge:read", "label": "知识读取", "kind": "menu", "parent": None},
    {"code": "knowledge:manage", "label": "维护知识与ACL", "kind": "button", "parent": "knowledge:read"},
]
PERMISSION_CODES = frozenset(item["code"] for item in PERMISSIONS)

class Input(BaseModel):
    """拒绝客户端伪造未声明的身份或授权字段。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Login(Input):
    """接收用户名和原样密码，不修剪密码字符。"""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)

class DepartmentCreate(Input):
    """描述新增部门与其直属父节点。"""
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = Field(default=None, gt=0)

class DepartmentUpdate(Input):
    """只修改明确提交的部门字段，允许移至根节点。"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: int | None = Field(default=None, gt=0)

class RoleCreate(Input):
    """描述角色名称及精确权限码集合。"""
    name: str = Field(min_length=1, max_length=100)
    permissions: list[str] = Field(default_factory=list)

class RoleUpdate(Input):
    """更新角色名称或权限集合。"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    permissions: list[str] | None = None

class UserCreate(BaseModel):
    """创建账号，密码保留空格且必须足够长。"""
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    department_id: int | None = Field(default=None, gt=0)
    role_ids: list[int] = Field(default_factory=list)
    is_active: bool = True

class UserUpdate(BaseModel):
    """更新账号信息；修改密码同时撤销旧登录令牌。"""
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    department_id: int | None = Field(default=None, gt=0)
    role_ids: list[int] | None = None
    is_active: bool | None = None

class ACLGrant(Input):
    """限制四维授权的类型与实体编号组合。"""
    type: Literal["global", "department", "role", "user"]
    entity_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_entity(self):
        """全局授权不指向实体，其他授权必须指向有效编号。"""
        if (self.type == "global") != (self.entity_id is None):
            raise ValueError("全局授权entity_id必须为空，其他授权必须指定entity_id")
        return self

class ACLUpdate(Input):
    """以完整集合替换知识单位授权。"""
    acl: list[ACLGrant]

class KnowledgeCreate(Input):
    """创建本阶段用于真实权限读取的文本知识单位。"""
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
    acl: list[ACLGrant] = Field(default_factory=list)

class KnowledgeUpdate(Input):
    """编辑文本知识，不伪造文件导入和版本索引。"""
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=100000)
