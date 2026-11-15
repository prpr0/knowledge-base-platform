"""知识库HTTP应用入口。"""
from contextlib import asynccontextmanager
import logging
import time
import traceback
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from .logging_setup import configure_logging, request_id
from .routes import build_router
from .database import make_engine, session_factory
from .settings import Settings
from .task_queue import enqueue_import

def create_app(settings: Settings | None = None) -> FastAPI:
    """建立显式配置的应用和数据库会话，不自动创建业务表。"""
    configure_logging()
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        """应用停止时关闭连接池。"""
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="知识库平台", lifespan=lifespan)
    app.state.engine = engine
    app.state.sessions = session_factory(engine)
    app.state.settings = settings
    app.state.enqueue_import = lambda task_id: enqueue_import(settings, task_id)
    app.include_router(build_router(app.state.sessions, settings, lambda task_id: app.state.enqueue_import(task_id)))
    logger = logging.getLogger(__name__)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError):
        """仅返回可操作的冲突提示，不泄露SQL参数和密码哈希。"""
        return JSONResponse(status_code=409, content={'detail': '名称重复或记录仍被引用，请检查后重试'})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        """删除校验错误中的原始输入，避免回显密码与令牌。"""
        errors = [{'loc': item['loc'], 'msg': item['msg'], 'type': item['type']} for item in error.errors()]
        return JSONResponse(status_code=422, content={'detail': errors})

    @app.middleware('http')
    async def trace_request(request: Request, call_next):
        """记录请求流程与安全堆栈，不记录凭证、请求正文或查询参数。"""
        identifier = uuid4().hex
        context_token = request_id.set(identifier)
        started = time.perf_counter()
        logger.info('流程=HTTP 步骤=开始 request_id=%s method=%s path=%s', identifier, request.method, request.url.path)
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                stack = ''.join(traceback.format_list(traceback.extract_tb(error.__traceback__)))
                logger.error('流程=HTTP 状态=失败 request_id=%s error_type=%s stack=%s', identifier, type(error).__name__, stack)
                response = JSONResponse(status_code=500, content={'detail': '服务处理失败，请凭request_id联系管理员'})
            level = logging.INFO if response.status_code < 400 else logging.WARNING
            if response.status_code >= 500:
                level = logging.ERROR
            logger.log(level, '流程=HTTP 步骤=结束 request_id=%s status=%s elapsed_ms=%.2f', identifier, response.status_code, (time.perf_counter() - started) * 1000)
            response.headers['X-Request-ID'] = identifier
            return response
        finally:
            request_id.reset(context_token)
    return app
