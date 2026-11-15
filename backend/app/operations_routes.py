"""实现FAQ人工发布、缺口补充、真实运营和独立审计接口。"""
import logging
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from .schemas import Input
from .models import FAQCandidate, FAQRelease, FAQSource, KnowledgeGap, MiningRun, QAAudit, SupplementTask, User
from .evidence import authorized_sources, public_citations
from .operations import gap_view, gap_visible, metrics_view, normalize_question, question_key, source_refs, supplement_view, utc_now
from .services import require

logger = logging.getLogger(__name__)


class ReviewInput(Input):
    """人工决定发布或拒绝，可润色问题、答案并指定完整来源。"""
    action: str = Field(pattern='^(publish|reject)$')
    question: str | None = Field(default=None, min_length=1, max_length=4000)
    answer: str | None = Field(default=None, min_length=1, max_length=30000)
    aliases: list[str] | None = Field(default=None, max_length=100)
    sources: list[dict] | None = Field(default=None, min_length=1, max_length=8)


class ReleaseInput(Input):
    """启停操作不修改已发布答案或来源快照。"""
    enabled: bool


class SupplementInput(Input):
    """可指派知识补充责任人，不接受伪造完成状态。"""
    title: str | None = Field(default=None, min_length=1, max_length=200)
    assigned_to: int | None = Field(default=None, gt=0)


def candidate_sources(session, actor, row):
    """候选的部门范围和完整来源同时满足才允许展示问题与草案。"""
    if row.department_id is not None and row.department_id != actor.department_id:
        return None
    if row.department_id is None and row.created_by_id != actor.id:
        return None
    return authorized_sources(session, actor, row.sources)


def candidate_view(row, sources):
    """只输出已通过候选作用域检查的文本与出处。"""
    return dict(id=row.id, question=row.question, answer=row.answer, aliases=row.aliases, sources=public_citations(sources),
        count=row.count, confidence=row.confidence, status=row.status, created_at=row.created_at)


def release_view(row, sources):
    """只展示当前仍获准的发布版本。"""
    return dict(id=row.id, question=row.question, answer=row.answer, aliases=row.aliases, version=row.version,
        enabled=row.enabled, sources=public_citations(sources), created_at=row.created_at)


def mining_view(row):
    """返回真实挖掘状态，不以已排队冒充完成。"""
    return dict(id=row.id, status=row.status, count=row.count, error=row.error, created_at=row.created_at, finished_at=row.finished_at)


def build_operations_router(settings, database, current_user, enqueue_ops):
    """所有入口分别执行动作权限与文字内容作用域检查。"""
    router = APIRouter()

    @router.get('/faq/candidates')
    def candidates(actor: User = Depends(current_user), session=Depends(database)):
        """筛掉缺源、撤权或跨部门候选，不暴露受限标题。"""
        require(actor, 'faq:manage')
        return [candidate_view(row, sources) for row in session.scalars(select(FAQCandidate).order_by(FAQCandidate.created_at.desc()))
            if (sources := candidate_sources(session, actor, row))]

    @router.post('/faq/mine', status_code=202)
    def mine(actor: User = Depends(current_user), session=Depends(database)):
        """提交真实Celery挖掘任务，失败状态持久化。"""
        require(actor, 'faq:manage')
        row = MiningRun(requested_by=actor.id)
        session.add(row)
        session.commit()
        try:
            enqueue_ops('mine', row.id)
        except Exception:
            row.status, row.error, row.finished_at = 'failed', '无法向任务队列提交挖掘，请检查Redis连接', utc_now()
            session.commit()
            logger.error('流程=FAQ挖掘 状态=排队失败 run_id=%s', row.id)
            raise HTTPException(503, '挖掘任务排队失败') from None
        logger.info('流程=FAQ挖掘 状态=已排队 run_id=%s user_id=%s', row.id, actor.id)
        return mining_view(row)

    @router.get('/faq/mining/{identifier}')
    def mining(identifier: str, actor: User = Depends(current_user), session=Depends(database)):
        """挖掘状态仅向有权限的发起人提供。"""
        require(actor, 'faq:manage')
        row = session.get(MiningRun, identifier)
        if row is None or row.requested_by != actor.id:
            raise HTTPException(404, '挖掘任务不存在')
        return mining_view(row)

    @router.post('/faq/candidates/{identifier}/review')
    def review(identifier: int, payload: ReviewInput, actor: User = Depends(current_user), session=Depends(database)):
        """只有人工审核通过才能新增不可变发布版；所有来源按AND鉴权。"""
        require(actor, 'faq:manage')
        row = session.get(FAQCandidate, identifier, with_for_update=True)
        sources = candidate_sources(session, actor, row) if row else None
        if not sources:
            raise HTTPException(404, '候选不存在或来源不可访问')
        if row.status != 'pending':
            raise HTTPException(409, '该候选已审核')
        if payload.action == 'reject':
            row.status, row.reviewer_id, row.reviewed_at = 'rejected', actor.id, utc_now()
            session.commit()
            return candidate_view(row, sources)
        sources = authorized_sources(session, actor, payload.sources or row.sources)
        if not sources:
            raise HTTPException(403, '所有答案来源均须当前可读')
        question, answer = payload.question or row.question, payload.answer or row.answer
        labels = set(re.findall(r'\[(S\d+)\]', answer))
        if not labels or not labels.issubset({ref['label'] for ref in sources}):
            raise HTTPException(422, '答案须包含有效引用，不能使用未知编号')
        aliases = payload.aliases if payload.aliases is not None else row.aliases
        if any(not item.strip() or len(item) > 4000 for item in aliases):
            raise HTTPException(422, '别名必须为1至4000字符')
        previous = list(session.scalars(select(FAQRelease).where(FAQRelease.question_key == question_key(question)).with_for_update()))
        # 同一问题可以属于不同权限范围；只替代来源集合相同的旧版。
        identity = {(ref['knowledge_id'], ref['version_id'], ref['chunk_id']) for ref in sources}
        versions = []
        for old in previous:
            if {(ref.knowledge_id, ref.version_id, ref.chunk_id) for ref in old.sources} == identity:
                old.enabled = False
                versions.append(old.version)
        release = FAQRelease(candidate_id=row.id, question=normalize_question(question), question_key=question_key(question), answer=answer,
            aliases=aliases, version=max(versions, default=0) + 1, reviewer_id=actor.id,
            sources=[FAQSource(**{key: ref[key] for key in ('knowledge_id', 'version_id', 'chunk_id', 'label')}) for ref in sources])
        session.add(release)
        row.status, row.reviewer_id, row.reviewed_at = 'published', actor.id, utc_now()
        session.commit()
        logger.info('流程=FAQ审核 状态=已发布 candidate_id=%s release_id=%s user_id=%s', identifier, release.id, actor.id)
        return release_view(release, sources)

    @router.get('/faq/releases')
    def releases(actor: User = Depends(current_user), session=Depends(database)):
        """列表只返回所有来源仍可访问的版本。"""
        require(actor, 'faq:manage')
        return [release_view(row, sources) for row in session.scalars(select(FAQRelease).order_by(FAQRelease.id.desc()))
            if (sources := authorized_sources(session, actor, source_refs(row)))]

    @router.patch('/faq/releases/{identifier}')
    def enable(identifier: int, payload: ReleaseInput, actor: User = Depends(current_user), session=Depends(database)):
        """无效或撤权来源不能通过重新启用绕过。"""
        require(actor, 'faq:manage')
        row = session.get(FAQRelease, identifier, with_for_update=True)
        sources = authorized_sources(session, actor, source_refs(row)) if row else None
        if not sources:
            raise HTTPException(404, 'FAQ不存在或来源不可访问')
        row.enabled = payload.enabled
        session.commit()
        return release_view(row, sources)

    @router.get('/gaps')
    def gaps(actor: User = Depends(current_user), session=Depends(database)):
        """缺口问题只在本人或直属部门作用域内显示。"""
        require(actor, 'gap:manage')
        return [gap_view(row) for row in session.scalars(select(KnowledgeGap).order_by(KnowledgeGap.last_seen.desc())) if gap_visible(actor, row)]

    @router.get('/gaps/{identifier}')
    def gap(identifier: int, actor: User = Depends(current_user), session=Depends(database)):
        """单项读取执行与缺口列表相同的作用域规则。"""
        require(actor, 'gap:manage')
        row = session.get(KnowledgeGap, identifier)
        if row is None or not gap_visible(actor, row):
            raise HTTPException(404, '缺口不存在')
        return gap_view(row)

    @router.post('/gaps/{identifier}/tasks')
    def supplement(identifier: int, payload: SupplementInput, actor: User = Depends(current_user), session=Depends(database)):
        """缺口行锁保证转任务幂等，责任人必须是当前活动账号。"""
        require(actor, 'gap:manage')
        row = session.get(KnowledgeGap, identifier, with_for_update=True)
        if row is None or not gap_visible(actor, row):
            raise HTTPException(404, '缺口不存在')
        old = session.scalar(select(SupplementTask).where(SupplementTask.gap_id == identifier, SupplementTask.status == 'open'))
        if old:
            return supplement_view(old)
        assignee = session.get(User, payload.assigned_to or actor.id)
        if assignee is None or not assignee.is_active:
            raise HTTPException(422, '受指派账号不存在或已停用')
        task = SupplementTask(gap_id=identifier, title=payload.title or row.question[:200], assigned_to=assignee.id, created_by_id=actor.id)
        session.add(task)
        session.commit()
        return supplement_view(task)

    @router.get('/supplement-tasks')
    def tasks(actor: User = Depends(current_user), session=Depends(database)):
        """受指派人或有该缺口作用域的管理员可查看补充任务。"""
        require(actor, 'gap:manage', 'knowledge:manage')
        return [supplement_view(task) for task in session.scalars(select(SupplementTask).order_by(SupplementTask.id.desc())) if task.assigned_to == actor.id or
            ('gap:manage' in {code for role in actor.roles for code in role.permissions} and gap_visible(actor, session.get(KnowledgeGap, task.gap_id)))]

    @router.get('/metrics')
    def metrics(days: int = 7, actor: User = Depends(current_user), session=Depends(database)):
        """指标按真实有效请求计算，空库保持零值与未知延时。"""
        if not 1 <= days <= 90:
            raise HTTPException(422, '统计窗口须为1至90天')
        return metrics_view(session, actor, days)

    @router.post('/metrics/aggregate', status_code=202)
    def aggregate(actor: User = Depends(current_user)):
        """触发真实每日聚合任务，不伪造快照。"""
        require(actor, 'metrics:read')
        try:
            enqueue_ops('aggregate', None)
        except Exception:
            raise HTTPException(503, '聚合任务排队失败') from None
        return {'status': 'queued'}

    @router.get('/audits')
    def audits(actor: User = Depends(current_user), session=Depends(database)):
        """候选及拦截ID仅此独立审计动作可读取。"""
        require(actor, 'audit:read')
        keys = ('request_id', 'conversation_id', 'message_id', 'user_id', 'department_id', 'question', 'candidates', 'allowed', 'blocked', 'token_usage', 'latency_ms', 'status', 'source_mode', 'model_called', 'model_version', 'error_code', 'created_at')
        return [{key: getattr(row, key) for key in keys} for row in session.scalars(select(QAAudit).order_by(QAAudit.created_at.desc()).limit(200))]

    return router
