"""本地Celery worker执行导入，数据库和Redis连接虚拟机实例。"""
from celery import Celery
from .settings import Settings
from .logging_setup import configure_logging
from .import_processing import process_task

settings = Settings.from_env()
configure_logging()
celery_app = Celery('knowledge-import-worker', broker=settings.redis_url)
celery_app.conf.update(task_serializer='json', accept_content=['json'], task_default_queue='knowledge_imports',
                       worker_prefetch_multiplier=1, timezone='UTC', task_ignore_result=True)

@celery_app.task(name='knowledge.import_task')
def import_documents(task_id: str) -> None:
    """消费真实队列消息，完成状态只从PostgreSQL读取。"""
    process_task(settings, task_id)
