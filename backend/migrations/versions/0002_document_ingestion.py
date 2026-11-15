"""保留已有知识并新增文档版本、真实检索切片和持久化导入任务。"""

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0002_document_ingestion"
down_revision = "0001_organization_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """先启用真实向量扩展，再按依赖创建新表及活动版本外键。"""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "knowledge",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "knowledge",
        sa.Column("category", sa.String(255), nullable=False, server_default=sa.text("'未分类'")),
    )
    op.add_column(
        "knowledge",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        "knowledge",
        sa.Column("file_format", sa.String(16), nullable=False, server_default=sa.text("'txt'")),
    )
    op.add_column("knowledge", sa.Column("active_version_id", sa.Integer(), nullable=True))
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("knowledge_id", sa.Integer(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'building'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("base_version_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint("knowledge_id", "number", name="uq_knowledge_versions_number"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("knowledge_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024).with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("search_vector", TSVECTOR().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.UniqueConstraint("version_id", "sequence", name="uq_chunks_sequence"),
    )
    op.create_index(
        "ix_chunks_search_vector", "chunks", ["search_vector"], postgresql_using="gin",
    )
    op.create_index(
        "ix_chunks_embedding", "chunks", ["embedding"], postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "import_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(255), nullable=False, server_default=sa.text("'未分类'")),
        sa.Column("acl", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "import_task_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("import_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("knowledge_id", sa.Integer(), sa.ForeignKey("knowledge.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("knowledge_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expected_active_version_id", sa.Integer(), nullable=True),
        sa.Column("operation", sa.String(32), nullable=False, server_default=sa.text("'create'")),
        sa.Column("replacement_content", sa.Text(), nullable=True),
        sa.Column("replacement_title", sa.String(255), nullable=True),
        sa.Column("replacement_chunk_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("stage", sa.String(32), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_foreign_key(
        "fk_knowledge_active_version", "knowledge", "knowledge_versions",
        ["active_version_id"], ["id"], ondelete="SET NULL", use_alter=True,
    )


def downgrade() -> None:
    """按反向依赖撤销本阶段结构，保留原组织、知识及授权记录。"""
    op.drop_constraint("fk_knowledge_active_version", "knowledge", type_="foreignkey")
    op.drop_table("import_task_items")
    op.drop_table("import_tasks")
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.drop_index("ix_chunks_search_vector", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("knowledge_versions")
    op.drop_column("knowledge", "active_version_id")
    op.drop_column("knowledge", "file_format")
    op.drop_column("knowledge", "updated_at")
    op.drop_column("knowledge", "category")
    op.drop_column("knowledge", "is_active")
