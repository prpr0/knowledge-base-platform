"""组织、账号及知识访问控制的持久化实体。"""

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, ForeignKey, Integer, String, Table, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """统一管理本项目实体的声明式元数据。"""


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Department(Base):
    """保存部门及其直属父部门关系。"""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )


class Role(Base):
    """保存角色及其显式授予的权限代码。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))


class User(Base):
    """保存账号状态、凭据版本与角色关联。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    permission_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    token_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin")


class Knowledge(Base):
    """保存知识正文及独立版本的访问控制条目。"""

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    acl_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    acl: Mapped[list["KnowledgeACL"]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan", lazy="selectin",
        passive_deletes=True,
    )


class KnowledgeACL(Base):
    """保存公开授权或指定部门、角色、用户的授权。"""

    __tablename__ = "knowledge_acl"
    __table_args__ = (
        CheckConstraint(
            "(type = 'global' AND entity_id IS NULL) OR "
            "(type IN ('department', 'role', 'user') AND entity_id IS NOT NULL AND entity_id > 0)",
            name="ck_knowledge_acl_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    knowledge: Mapped[Knowledge] = relationship(back_populates="acl")
