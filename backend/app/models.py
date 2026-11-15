"""组织、账号、知识版本、索引切片及文档导入任务的持久化实体。"""

from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer,
    String, Table, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _new_uuid() -> str:
    """为每个导入任务及子项独立生成字符串形式的唯一标识。"""
    return str(uuid4())


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
    """保存知识当前正文、活动版本及独立维护的访问控制条目。"""

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    acl_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    category: Mapped[str] = mapped_column(
        String(255), default="未分类", server_default=text("'未分类'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    file_format: Mapped[str] = mapped_column(String(16), default="txt", server_default=text("'txt'"))
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "knowledge_versions.id", ondelete="SET NULL", use_alter=True,
            name="fk_knowledge_active_version",
        ), nullable=True,
    )
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


class KnowledgeVersion(Base):
    """保存候选或已发布的知识快照及其原文件出处。"""

    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "number", name="uq_knowledge_versions_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    file_format: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    source_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default="building", server_default=text("'building'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", lazy="selectin",
        passive_deletes=True,
    )


class Chunk(Base):
    """保存版本内有序切片、出处与真实向量及全文检索索引。"""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "sequence", name="uq_chunks_sequence"),
        Index(
            "ix_chunks_search_vector", "search_vector", postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_chunks_embedding", "embedding", postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    section: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024).with_variant(JSON(), "sqlite"), nullable=True
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR().with_variant(Text(), "sqlite"), nullable=True
    )
    version: Mapped[KnowledgeVersion] = relationship(back_populates="chunks")


class ImportTask(Base):
    """保存导入发起人、分类、授权与持久化任务汇总状态。"""

    __tablename__ = "import_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    requester_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(
        String(255), default="未分类", server_default=text("'未分类'")
    )
    acl: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    items: Mapped[list["ImportTaskItem"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin",
        passive_deletes=True,
    )


class ImportTaskItem(Base):
    """保存单文件导入或重建所需快照、进度、失败原因与重试次数。"""

    __tablename__ = "import_task_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    task_id: Mapped[str] = mapped_column(ForeignKey("import_tasks.id", ondelete="CASCADE"))
    relative_path: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_format: Mapped[str] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64), default="", server_default=text("''"))
    knowledge_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge.id", ondelete="SET NULL"), nullable=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="SET NULL"), nullable=True
    )
    expected_active_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(32), default="create", server_default=text("'create'"))
    replacement_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    replacement_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    replacement_chunk_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default=text("'queued'"))
    stage: Mapped[str] = mapped_column(String(32), default="queued", server_default=text("'queued'"))
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    task: Mapped[ImportTask] = relationship(back_populates="items")
