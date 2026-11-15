"""将已有文本知识提交真实索引重建任务，不生成伪向量或自动发布。"""
import argparse
from sqlalchemy import select
from .database import make_engine, session_factory
from .ingestion import dispatch_or_fail, new_rebuild
from .logging_setup import configure_logging
from .models import ImportTaskItem, Knowledge, User
from .services import require
from .settings import Settings
from .task_queue import enqueue_import


def main() -> int:
    """以现有有权账号重建尚无活动版本的旧文本单位。"""
    parser = argparse.ArgumentParser(description='已有文本知识索引回填')
    parser.add_argument('--actor', required=True, help='具有knowledge:manage的现有用户名')
    args = parser.parse_args()
    configure_logging()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        with session_factory(engine)() as session:
            actor = session.scalar(select(User).where(User.username == args.actor, User.is_active.is_(True)))
            if actor is None:
                raise ValueError('有效账号不存在')
            require(actor, 'knowledge:manage')
            records = list(session.scalars(select(Knowledge).where(Knowledge.active_version_id.is_(None), Knowledge.is_active.is_(True), Knowledge.content != '').order_by(Knowledge.id)))
            for item in records:
                pending = session.scalar(select(ImportTaskItem.id).where(ImportTaskItem.knowledge_id == item.id, ImportTaskItem.status.in_(['queued', 'running'])))
                if pending:
                    continue
                task = new_rebuild(session, settings, actor.id, item)
                dispatch_or_fail(session, task, lambda task_id: enqueue_import(settings, task_id))
                print('已提交真实索引任务', task.id, 'knowledge_id', item.id)
        return 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
