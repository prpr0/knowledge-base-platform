"""保存组织、知识导入、会话、模型配置及问答运营的持久化实体。"""

from datetime import date, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey, Index, Integer,
    String, Table, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _new_uuid() -> str:
    """为导入任务、子项及挖掘任务独立生成字符串形式的唯一标识。"""
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
    supplement_task_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "supplement_tasks.id", ondelete="SET NULL",
            name="fk_import_tasks_supplement_task_id",
        ), nullable=True,
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


class Conversation(Base):
    """保存归属单个用户的会话及其可编辑标题。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(
        String(200), default="新会话", server_default=text("'新会话'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """保存消息正文、处理状态及需在读取时重新鉴权的引用快照。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(32), default="complete", server_default=text("'complete'")
    )
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    request_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )


class ModelConfiguration(Base):
    """保存模型服务配置、加密密钥及真实发布与校准验证状态。"""

    __tablename__ = "model_configurations"
    __table_args__ = (
        CheckConstraint("mode IN ('finetuned', 'external_api')", name="ck_model_configurations_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1, server_default=text("1"))
    base_url: Mapped[str] = mapped_column(String(512), default="", server_default=text("''"))
    mode: Mapped[str] = mapped_column(String(32), default="finetuned", server_default=text("'finetuned'"))
    model_name: Mapped[str] = mapped_column(
        String(128), default="kb-grounded-qwen3-4b-v1",
        server_default=text("'kb-grounded-qwen3-4b-v1'"),
    )
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    release_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    calibration_manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )


class FAQCandidate(Base):
    """保存语义聚类候选、部门快照及人工审核决策。"""

    __tablename__ = "faq_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    sources: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    count: Mapped[int] = mapped_column(Integer)
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default=text("'pending'")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cluster_key: Mapped[str] = mapped_column(String(64), unique=True)


class FAQRelease(Base):
    """保存经人工发布的答案版本及需要逐一鉴权的完整来源。"""

    __tablename__ = "faq_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(
        ForeignKey("faq_candidates.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(64), index=True)
    answer: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    sources: Mapped[list["FAQSource"]] = relationship(
        back_populates="faq", cascade="all, delete-orphan", lazy="selectin", passive_deletes=True
    )


class FAQSource(Base):
    """保留全部发布来源，原知识删除后以空外键阻止残缺来源通过鉴权。"""

    __tablename__ = "faq_sources"
    __table_args__ = (UniqueConstraint("faq_id", "label", name="uq_faq_sources_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    faq_id: Mapped[int] = mapped_column(ForeignKey("faq_releases.id", ondelete="CASCADE"))
    knowledge_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge.id", ondelete="SET NULL"), nullable=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="SET NULL"), nullable=True
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(16))
    faq: Mapped[FAQRelease] = relationship(back_populates="sources")


class QAAudit(Base):
    """以唯一请求标识保存问答计数、权限决策及真实模型消耗审计。"""

    __tablename__ = "qa_audits"

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    allowed: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    blocked: Mapped[list[int]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    sources: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    source_mode: Mapped[str] = mapped_column(String(32))
    faq_release_id: Mapped[int | None] = mapped_column(
        ForeignKey("faq_releases.id", ondelete="SET NULL"), nullable=True
    )
    valid: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    model_called: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )


class KnowledgeGap(Base):
    """按问题及直属部门快照聚合缺口并跟踪知识补充结果。"""

    __tablename__ = "knowledge_gaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    question_key: Mapped[str] = mapped_column(String(64))
    scope_key: Mapped[str] = mapped_column(String(64), unique=True)
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    frequency: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    highest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_category: Mapped[str] = mapped_column(
        String(255), default="未分类", server_default=text("'未分类'")
    )
    status: Mapped[str] = mapped_column(String(32), default="open", server_default=text("'open'"))


class SupplementTask(Base):
    """保存缺口补充任务、指派关系及成功导入的知识标识。"""

    __tablename__ = "supplement_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    gap_id: Mapped[int] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    assigned_to: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="open", server_default=text("'open'"))
    knowledge_ids: Mapped[list[int]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MiningRun(Base):
    """保存真实语义挖掘任务的状态、候选数量和失败原因。"""

    __tablename__ = "mining_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default=text("'queued'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class MetricSnapshot(Base):
    """按UTC日期保存不包含受限标题的纯数值运营快照。"""

    __tablename__ = "metric_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    total_requests: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    uv: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    faq_hits: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    covered: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    permission_blocked: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    token_total: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    token_unknown: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    latencies: Mapped[list[float]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), server_default=func.now(), onupdate=func.now()
    )
