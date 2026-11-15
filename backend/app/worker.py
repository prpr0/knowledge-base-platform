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

@celery_app.task(name='knowledge.mine_faq')
def mine_faq(run_id: str) -> None:
    """执行真实BGE语义挖掘，状态写入PostgreSQL。"""
    from .maintenance import run_mining
    run_mining(settings, run_id)

@celery_app.task(name='knowledge.aggregate_metrics')
def aggregate_metrics() -> None:
    """聚合真实问答日志，不产生演示指标。"""
    from .maintenance import aggregate_metrics as aggregate
    aggregate(settings)
