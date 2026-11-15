"""向虚拟机独立Redis发送真实Celery导入任务。"""
from functools import lru_cache
from celery import Celery

@lru_cache(maxsize=1)
def queue_client(redis_url: str) -> Celery:
    """复用仅用于发送的队列客户端，不将Redis视作任务状态库。"""
    app = Celery('knowledge-import-producer', broker=redis_url)
    app.conf.update(task_serializer='json', accept_content=['json'], task_publish_retry=False, broker_connection_timeout=3)
    return app

def enqueue_import(settings, task_id: str) -> None:
    """队列异常向上抛出，由API保存明确失败状态。"""
    queue_client(settings.redis_url).send_task('knowledge.import_task', args=[task_id], queue='knowledge_imports', retry=False)
