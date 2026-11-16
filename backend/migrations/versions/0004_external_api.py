"""为模型配置增加外部API模式，已有配置继续使用微调模式。"""
from alembic import op
import sqlalchemy as sa

revision = '0004_external_api'
down_revision = '0003_qa_operations'
branch_labels = None
depends_on = None


def upgrade():
    """以数据库默认值兼容旧行并限制合法模式。"""
    op.add_column('model_configurations', sa.Column('mode', sa.String(32), nullable=False, server_default='finetuned'))
    op.create_check_constraint('ck_model_configurations_mode', 'model_configurations', "mode IN ('finetuned', 'external_api')")


def downgrade():
    """移除模式约束和字段，恢复原配置结构。"""
    op.drop_constraint('ck_model_configurations_mode', 'model_configurations', type_='check')
    op.drop_column('model_configurations', 'mode')
