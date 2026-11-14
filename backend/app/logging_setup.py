"""从独立配置初始化日志并传递安全的请求关联标识。"""
from contextvars import ContextVar
from pathlib import Path
import logging.config
import yaml

request_id = ContextVar('request_id', default='-')

def configure_logging() -> None:
    """统一加载日志设置，不在业务模块重复安装处理器。"""
    path = Path(__file__).resolve().parents[2] / 'deploy' / 'logging.yaml'
    with path.open(encoding='utf-8') as stream:
        logging.config.dictConfig(yaml.safe_load(stream))
