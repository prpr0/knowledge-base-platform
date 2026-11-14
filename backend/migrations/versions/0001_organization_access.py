"""创建组织、账号、角色与知识授权的初始表结构。"""

from alembic import op
import sqlalchemy as sa

revision = "0001_organization_access"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """按外键依赖顺序创建业务表及完整数据库约束。"""
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("permission_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "knowledge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("acl_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_table(
        "knowledge_acl",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("knowledge_id", sa.Integer(), sa.ForeignKey("knowledge.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "(type = 'global' AND entity_id IS NULL) OR "
            "(type IN ('department', 'role', 'user') AND entity_id IS NOT NULL AND entity_id > 0)",
            name="ck_knowledge_acl_target",
        ),
    )


def downgrade() -> None:
    """按反向依赖顺序删除本次迁移创建的全部业务表。"""
    op.drop_table("knowledge_acl")
    op.drop_table("knowledge")
    op.drop_table("user_roles")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_table("departments")
