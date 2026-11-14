"""通过显式环境变量配置在线与离线数据库迁移。"""

import os

from alembic import context

from backend.app.database import make_engine
from backend.app.models import Base


def database_url() -> str:
    """读取数据库地址，缺失时明确失败且不记录地址内容。"""
    url = os.environ.get("DATABASE_URL")
    if not url or not url.strip():
        raise RuntimeError("执行数据库迁移必须设置 DATABASE_URL 环境变量")
    return url


def run_migrations_offline() -> None:
    """生成离线迁移 SQL，不建立数据库连接。"""
    context.configure(
        url=database_url(), target_metadata=Base.metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在显式创建的连接中执行迁移，并确保释放引擎。"""
    engine = make_engine(database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
