"""显式创建数据库引擎与会话工厂，不在导入时访问数据库。"""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> Engine:
    """创建引擎，并为 SQLite 隔离测试开启外键与内存连接复用。"""
    parsed = make_url(url)
    options = {}
    if parsed.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:") or parsed.query.get("mode") == "memory":
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if parsed.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(connection, connection_record) -> None:
    """为每个 SQLite 连接开启外键约束，保持与正式数据库一致。"""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """返回提交后仍可读取已加载实体属性的会话工厂。"""
    return sessionmaker(bind=engine, expire_on_commit=False)
