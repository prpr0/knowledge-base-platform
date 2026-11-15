"""文档导入、版本切片、受控下载与真实检索接口。"""
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from .documents import DocumentError
from .ingestion import dispatch_or_fail, new_import, new_rebuild, retry_failed, storage_path, task_view
from .models import Chunk, ImportTask, Knowledge, KnowledgeVersion, User
from .schemas import ACLGrant
from .services import get_record, permissions, readable, require
from .search import hybrid_search

class ChunkEdit(BaseModel):
    """切片修订只能提交完整新正文，不接受客户端角色。"""
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=100000)

class SearchInput(BaseModel):
    """约束检索问题与输出数量，权限仅取自服务端身份。"""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=8, ge=1, le=8)


def build_ingestion_router(settings, database, current_user, enqueue) -> APIRouter:
    """复用组织阶段的真实登录态与数据库会话。"""
    router = APIRouter()

    def visible_task(session, task_id: str, actor: User) -> ImportTask:
        """任务详情只对发起人或知识管理者可见。"""
        task = get_record(session, ImportTask, task_id)
        if task.requester_id != actor.id and 'knowledge:manage' not in permissions(actor):
            raise HTTPException(403, '无权查看该导入任务')
        return task

    @router.post('/imports', status_code=202)
    def create_import(files: list[UploadFile] = File(...), paths: str = Form(...), acl: str = Form('[]'), category: str = Form('未分类'),
                      supplement_task_id: int | None = Form(None, gt=0), actor: User = Depends(current_user), session: Session = Depends(database)):
        """持久化批量上传后发真实队列消息，不把上传完成当作导入成功。"""
        require(actor, 'knowledge:manage')
        if supplement_task_id is not None:
            from .operations import check_supplement_task
            check_supplement_task(session, actor, supplement_task_id)
        try:
            relative_paths = TypeAdapter(list[str]).validate_json(paths)
            grants = TypeAdapter(list[ACLGrant]).validate_json(acl)
        except (ValueError, ValidationError):
            raise HTTPException(422, 'paths或acl格式不正确') from None
        if not files or len(files) != len(relative_paths):
            raise HTTPException(422, '每个文件必须提供一一对应的相对路径')
        category = category.strip()
        if not category or len(category) > 255:
            raise HTTPException(422, '分类必须为1至255字符')
        try:
            task = new_import(session, settings, actor.id, list(zip(relative_paths, [file.file for file in files])), grants, category)
        except DocumentError as error:
            raise HTTPException(422, str(error)) from None
        task.supplement_task_id = supplement_task_id
        dispatch_or_fail(session, task, enqueue)
        return task_view(task)

    @router.get('/imports')
    def imports(actor: User = Depends(current_user), session: Session = Depends(database)):
        """从PG读取任务列表，普通发起人只能查看自己的任务。"""
        query = select(ImportTask).order_by(ImportTask.created_at.desc())
        if 'knowledge:manage' not in permissions(actor):
            query = query.where(ImportTask.requester_id == actor.id)
        return [task_view(task) for task in session.scalars(query)]

    @router.get('/imports/{task_id}')
    def import_detail(task_id: str, actor: User = Depends(current_user), session: Session = Depends(database)):
        """返回真实阶段、失败原因及逐文件进度。"""
        return task_view(visible_task(session, task_id, actor))

    @router.post('/imports/{task_id}/retry', status_code=202)
    def retry_import(task_id: str, actor: User = Depends(current_user), session: Session = Depends(database)):
        """管理者显式重试失败子项，不重复已发布成功项。"""
        require(actor, 'knowledge:manage')
        task = visible_task(session, task_id, actor)
        session.refresh(task, with_for_update=True)
        retry_failed(task)
        dispatch_or_fail(session, task, enqueue)
        return task_view(task)

    @router.get('/knowledge/{record_id}/versions')
    def versions(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """管理版本记录不隐式返回旧正文或切片。"""
        require(actor, 'knowledge:manage')
        get_record(session, Knowledge, record_id)
        return [dict(id=version.id, number=version.number, title=version.title, status=version.status,
                     created_at=version.created_at, error=version.error, file_format=version.file_format,
                     sha256=version.sha256) for version in session.scalars(select(KnowledgeVersion).where(
                         KnowledgeVersion.knowledge_id == record_id).order_by(KnowledgeVersion.number.desc()))]

    @router.get('/knowledge/{record_id}/chunks')
    def chunks(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """切片只从当前活动且获准的版本读取。"""
        item = get_record(session, Knowledge, record_id)
        if not readable(actor, item):
            raise HTTPException(403, '无权读取该知识切片')
        return [dict(id=chunk.id, version_id=chunk.version_id, sequence=chunk.sequence, content=chunk.content,
                     section=chunk.section, page=chunk.page, location=chunk.location, token_count=chunk.token_count)
                for chunk in session.scalars(select(Chunk).where(Chunk.version_id == item.active_version_id).order_by(Chunk.sequence))]

    @router.patch('/chunks/{chunk_id}', status_code=202)
    def edit_chunk(chunk_id: int, payload: ChunkEdit, actor: User = Depends(current_user), session: Session = Depends(database)):
        """人工修订生成独立文本快照并全量重建新版本索引。"""
        require(actor, 'knowledge:manage')
        if not payload.content.strip():
            raise HTTPException(422, '切片正文不能为空')
        chunk = get_record(session, Chunk, chunk_id)
        version = get_record(session, KnowledgeVersion, chunk.version_id)
        item = get_record(session, Knowledge, version.knowledge_id, True)
        if item.active_version_id != version.id or not item.is_active:
            raise HTTPException(409, '只能编辑启用知识的当前活动切片')
        task = new_rebuild(session, settings, actor.id, item, content=payload.content, chunk_id=chunk_id)
        dispatch_or_fail(session, task, enqueue)
        return task_view(task)

    @router.get('/knowledge/{record_id}/download')
    def download(record_id: int, actor: User = Depends(current_user), session: Session = Depends(database)):
        """每次下载重新检查正文ACL，原文件不公开为静态目录。"""
        item = get_record(session, Knowledge, record_id)
        if not readable(actor, item):
            raise HTTPException(403, '无权下载该知识原文件')
        version = session.get(KnowledgeVersion, item.active_version_id)
        if version is None or not version.storage_key:
            raise HTTPException(404, '该版本没有可下载的原文件')
        path = storage_path(settings, version.storage_key)
        if not path.is_file():
            raise HTTPException(404, '原文件不存在，请联系管理员')
        return FileResponse(path, filename=Path(version.source_path).name, media_type='application/octet-stream')

    @router.post('/search')
    def search(payload: SearchInput, actor: User = Depends(current_user), session: Session = Depends(database)):
        """仅返回授权片段，受限候选不送入重排或客户端。"""
        require(actor, 'knowledge:read')
        return hybrid_search(session, settings, actor, payload.query, payload.limit)

    return router
