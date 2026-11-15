"""新增会话、安全引用、FAQ和真实运营表，保留原有业务数据。"""
from alembic import op
import sqlalchemy as sa

revision = '0003_qa_operations'
down_revision = '0002_document_ingestion'
branch_labels = None
depends_on = None


def ident():
    """为新业务表声明整数主键。"""
    return sa.Column('id', sa.Integer(), primary_key=True)


def stamp(name='created_at'):
    """所有新增时间均使用数据库UTC兼容时间戳。"""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def reference(name, target, nullable=True, ondelete='SET NULL'):
    """显式声明可追踪删除行为的外键。"""
    return sa.Column(name, sa.Integer(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def json_column(name):
    """为数组快照提供合法JSON空值。"""
    return sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'"))


def string(name, default, length=32):
    """为有稳定初始状态的文本字段提供数据库默认值。"""
    return sa.Column(name, sa.String(length), nullable=False, server_default=default)


def counter(name, default='0'):
    """为真实计数定义零值，不生成示例记录。"""
    return sa.Column(name, sa.Integer(), nullable=False, server_default=default)


def upgrade():
    """按外键依赖创建新实体并向已有导入表添加任务关联。"""
    op.create_table('conversations', ident(), reference('user_id', 'users.id', False, 'CASCADE'),
        string('title', '新会话', 200), stamp(), stamp('updated_at'))
    op.create_table('messages', ident(), reference('conversation_id', 'conversations.id', False, 'CASCADE'),
        sa.Column('role', sa.String(32), nullable=False), sa.Column('content', sa.Text(), nullable=False, server_default=''),
        string('status', 'complete'), json_column('citations'), sa.Column('request_id', sa.String(32)),
        sa.Column('model_version', sa.String(128)), sa.Column('source_mode', sa.String(32)), stamp())
    op.create_table('model_configurations', ident(), string('base_url', '', 512),
        string('model_name', 'kb-grounded-qwen3-4b-v1', 128), sa.Column('api_key_ciphertext', sa.Text()),
        sa.Column('release_manifest_path', sa.Text()), sa.Column('release_manifest_hash', sa.String(64)),
        sa.Column('release_version', sa.String(128)), sa.Column('release_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('calibration_manifest_path', sa.Text()), sa.Column('score_threshold', sa.Float()),
        sa.Column('calibration_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        reference('updated_by', 'users.id'), stamp('updated_at'))
    op.create_table('faq_candidates', ident(), sa.Column('question', sa.Text(), nullable=False),
        sa.Column('question_key', sa.String(64), nullable=False), sa.Column('answer', sa.Text(), nullable=False),
        json_column('aliases'), json_column('sources'), sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer()), sa.Column('confidence', sa.Float(), nullable=False), string('status', 'pending'),
        reference('created_by_id', 'users.id'),
        reference('reviewer_id', 'users.id'), stamp(), sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('cluster_key', sa.String(64), nullable=False, unique=True))
    op.create_table('faq_releases', ident(), reference('candidate_id', 'faq_candidates.id'),
        sa.Column('question', sa.Text(), nullable=False), sa.Column('question_key', sa.String(64), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False), json_column('aliases'), counter('version', '1'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()), reference('reviewer_id', 'users.id'), stamp())
    op.create_index('ix_faq_releases_question_key', 'faq_releases', ['question_key'])
    op.create_table('faq_sources', ident(), reference('faq_id', 'faq_releases.id', False, 'CASCADE'),
        reference('knowledge_id', 'knowledge.id'), reference('version_id', 'knowledge_versions.id'),
        reference('chunk_id', 'chunks.id'), sa.Column('label', sa.String(16), nullable=False),
        sa.UniqueConstraint('faq_id', 'label', name='uq_faq_sources_label'))
    op.create_table('qa_audits', sa.Column('request_id', sa.String(32), primary_key=True),
        reference('conversation_id', 'conversations.id'), reference('message_id', 'messages.id'), reference('user_id', 'users.id'),
        sa.Column('department_id', sa.Integer()), sa.Column('question', sa.Text(), nullable=False),
        json_column('candidates'), json_column('allowed'), json_column('blocked'), json_column('sources'),
        sa.Column('token_usage', sa.JSON()), sa.Column('latency_ms', sa.Float()),
        sa.Column('status', sa.String(32), nullable=False), sa.Column('source_mode', sa.String(32), nullable=False),
        reference('faq_release_id', 'faq_releases.id'), sa.Column('valid', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('model_called', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('model_version', sa.String(128)), sa.Column('error_code', sa.String(64)), stamp())
    op.create_table('knowledge_gaps', ident(), sa.Column('question', sa.Text(), nullable=False),
        sa.Column('question_key', sa.String(64), nullable=False), sa.Column('scope_key', sa.String(64), nullable=False, unique=True),
        sa.Column('department_id', sa.Integer()), reference('user_id', 'users.id'), counter('frequency', '1'),
        stamp('first_seen'), stamp('last_seen'), sa.Column('highest_score', sa.Float()),
        string('suggested_category', '未分类', 255), string('status', 'open'))
    op.create_table('supplement_tasks', ident(), reference('gap_id', 'knowledge_gaps.id', False, 'CASCADE'),
        sa.Column('title', sa.String(200), nullable=False), reference('assigned_to', 'users.id'),
        reference('created_by_id', 'users.id'), string('status', 'open'), json_column('knowledge_ids'),
        stamp(), sa.Column('completed_at', sa.DateTime(timezone=True)))
    op.add_column('import_tasks', sa.Column('supplement_task_id', sa.Integer()))
    op.create_foreign_key('fk_import_tasks_supplement_task_id', 'import_tasks', 'supplement_tasks', ['supplement_task_id'], ['id'], ondelete='SET NULL')
    op.create_table('mining_runs', sa.Column('id', sa.String(36), primary_key=True), reference('requested_by', 'users.id'),
        string('status', 'queued'), stamp(), sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('error', sa.Text()), counter('count'))
    op.create_table('metric_snapshots', sa.Column('day', sa.Date(), primary_key=True),
        counter('total_requests'), counter('uv'), counter('faq_hits'), counter('covered'), counter('permission_blocked'),
        counter('token_total'), counter('token_unknown'), json_column('latencies'), stamp('as_of'))


def downgrade():
    """仅撤销本次结构，原知识、组织与导入记录保持不变。"""
    op.drop_table('metric_snapshots')
    op.drop_table('mining_runs')
    op.drop_constraint('fk_import_tasks_supplement_task_id', 'import_tasks', type_='foreignkey')
    op.drop_column('import_tasks', 'supplement_task_id')
    for name in ('supplement_tasks', 'knowledge_gaps', 'qa_audits', 'faq_sources', 'faq_releases', 'faq_candidates', 'model_configurations', 'messages', 'conversations'):
        op.drop_table(name)
