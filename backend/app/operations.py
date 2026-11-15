"""FAQ来源逐项鉴权、真实缓存、缺口闭环与运营计数的共享服务。"""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
import logging
import math
import unicodedata

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .evidence import authorized_sources
from .models import FAQRelease, Knowledge, KnowledgeGap, QAAudit, SupplementTask, User
from .services import permissions, readable, require

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """统一生成带UTC时区的业务时间。"""
    return datetime.now(timezone.utc)


def normalize_question(question: str) -> str:
    """仅规范兼容字符和首尾空白，保留命令标点及大小写。"""
    return unicodedata.normalize('NFKC', question).strip()


def question_key(question: str) -> str:
    """用规范化问题生成稳定索引，不把问题原文写入缓存键。"""
    return sha256(normalize_question(question).encode('utf-8')).hexdigest()


def source_refs(release: FAQRelease) -> list[dict]:
    """完整保留发布来源，包括已删除资料留下的空引用。"""
    return [dict(knowledge_id=source.knowledge_id, version_id=source.version_id,
                 chunk_id=source.chunk_id, label=source.label) for source in release.sources]


def find_faq(session: Session, settings, actor: User, question: str) -> dict | None:
    """精确命中不可变发布版，Redis命中前后都以当前全部来源鉴权。"""
    session.refresh(actor)
    session.expire(actor, ['roles'])
    if not actor.is_active:
        return None
    normalized = normalize_question(question)
    releases = session.scalars(select(FAQRelease).where(FAQRelease.enabled.is_(True))
                               .order_by(FAQRelease.version.desc(), FAQRelease.id.desc())
                               .execution_options(populate_existing=True))
    for release in releases:
        if normalized not in {normalize_question(text) for text in [release.question, *release.aliases]}:
            continue
        refs = source_refs(release)
        sources = authorized_sources(session, actor, refs) if refs else None
        if not sources:
            continue
        identity = dict(user=actor.id, permission_version=actor.permission_version,
                        department=actor.department_id, roles=sorted(role.id for role in actor.roles),
                        permissions=sorted(permissions(actor)), release=release.id, version=release.version,
                        sources=[{key: source.get(key) for key in
                                  ('knowledge_id', 'version_id', 'chunk_id', 'acl_version', 'label')}
                                 for source in sources])
        digest = sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        cache_key = f'knowledge:faq:v1:{digest}'
        expected = dict(release_id=release.id, answer=release.answer)
        try:
            with Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=3) as cache:
                cached = cache.get(cache_key)
                try:
                    value = json.loads(cached) if cached is not None else None
                except (TypeError, ValueError):
                    value = None
                if value != expected:
                    cache.set(cache_key, json.dumps(expected, ensure_ascii=False), ex=300)
        except RedisError as exc:
            logger.error('FAQ缓存访问失败 release_id=%s', release.id)
            raise HTTPException(503, 'FAQ缓存服务不可用') from exc
        # 缓存只保存不可变答案；正文来源始终取本次事务的鉴权结果。
        return dict(**expected, sources=sources)
    return None


def record_gap(session: Session, actor: User, question: str, highest_score=None) -> KnowledgeGap:
    """按规范化问题和直属部门聚合真实无证据请求，调用方负责排除纯权限失败。"""
    normalized = normalize_question(question)
    if not normalized:
        raise ValueError('缺口问题不能为空')
    if highest_score is not None and not math.isfinite(highest_score):
        raise ValueError('缺口分数必须为有限数')
    identity_scope = f'department:{actor.department_id}' if actor.department_id is not None else f'user:{actor.id}'
    scope = sha256(f'{identity_scope}:{question_key(normalized)}'.encode()).hexdigest()
    # 唯一作用域的数据库冲突更新同时覆盖首次插入竞争与频次丢失。
    values = dict(question=normalized, question_key=question_key(normalized), scope_key=scope,
                  department_id=actor.department_id, user_id=actor.id, frequency=1,
                  first_seen=utc_now(), last_seen=utc_now(), highest_score=highest_score,
                  suggested_category='未分类', status='open')
    dialect = session.get_bind().dialect.name
    if dialect in ('postgresql', 'sqlite'):
        if dialect == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        from sqlalchemy import case
        statement = insert(KnowledgeGap).values(**values)
        score = KnowledgeGap.highest_score
        updated_score = score if highest_score is None else case(
            (score.is_(None), highest_score), (score < highest_score, highest_score), else_=score)
        statement = statement.on_conflict_do_update(index_elements=['scope_key'], set_={
            'frequency': KnowledgeGap.frequency + 1, 'last_seen': values['last_seen'],
            'highest_score': updated_score, 'status': 'open'})
        session.execute(statement)
        gap = session.scalar(select(KnowledgeGap).where(KnowledgeGap.scope_key == scope)
                             .execution_options(populate_existing=True))
    else:
        raise RuntimeError('缺口聚合需要支持原子冲突更新的数据库')
    session.flush()
    return gap


def gap_visible(actor: User, gap: KnowledgeGap) -> bool:
    """缺口运营仅限本人或同一非空直属部门，空部门不构成共享范围。"""
    return gap.user_id == actor.id or (actor.department_id is not None and
                                      gap.department_id == actor.department_id)


def check_supplement_task(session: Session, actor: User, task_id: int) -> SupplementTask:
    """导入关联任务只允许当前受指派人或有该缺口管理权限的当前身份。"""
    task = session.get(SupplementTask, task_id, with_for_update=True)
    if task is None:
        raise HTTPException(404, '补充任务不存在')
    gap = session.get(KnowledgeGap, task.gap_id)
    if task.assigned_to != actor.id and not (
            'gap:manage' in permissions(actor) and gap is not None and gap_visible(actor, gap)):
        raise HTTPException(403, '没有该补充任务权限')
    if task.status != 'open':
        raise HTTPException(409, '补充任务已完成')
    return task


def complete_supplement_task(session: Session, task_id: int, knowledge_id: int) -> None:
    """整个导入成功后关联真实活动知识，并在同一事务内关闭补充任务与缺口。"""
    task = session.get(SupplementTask, task_id, with_for_update=True)
    if task is None:
        raise ValueError('补充任务不存在')
    knowledge = session.get(Knowledge, knowledge_id)
    if knowledge is None or not knowledge.is_active or knowledge.active_version_id is None:
        raise ValueError('补充任务只能关联已经发布的活动知识')
    task.knowledge_ids = sorted(set(task.knowledge_ids or []) | {knowledge_id})
    task.status = 'completed'
    task.completed_at = task.completed_at or utc_now()
    gap = session.get(KnowledgeGap, task.gap_id, with_for_update=True)
    gap.status = 'resolved'
    session.flush()


def supplement_view(task: SupplementTask) -> dict:
    """输出真实补充任务进度与关联知识编号。"""
    return {name: getattr(task, name) for name in ('id', 'gap_id', 'title', 'assigned_to',
            'status', 'knowledge_ids', 'created_at', 'completed_at')}


def gap_view(gap: KnowledgeGap) -> dict:
    """输出已通过作用域检查的缺口记录。"""
    return {name: getattr(gap, name) for name in ('id', 'question', 'department_id', 'user_id',
            'frequency', 'first_seen', 'last_seen', 'highest_score', 'suggested_category', 'status')}


def metrics_start(days: int, now: datetime) -> datetime:
    """按UTC日界计算包含今日的固定天数窗口。"""
    return datetime.combine(now.date() - timedelta(days=days - 1), time.min, timezone.utc)


def audit_tokens(audit: QAAudit) -> int | None:
    """仅累计真实usage，已知未调用模型的请求消耗为零。"""
    usage = audit.token_usage
    if isinstance(usage, dict):
        total = usage.get('total_tokens')
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            return total
        prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
               for value in (prompt, completion)):
            return prompt + completion
    return None if audit.model_called else 0


def summarize_audits(audits: list[QAAudit]) -> dict:
    """以request_id去重计算API与每日快照共用的真实计数。"""
    rows = list({row.request_id: row for row in audits if row.valid}.values())
    token_values = [audit_tokens(row) for row in rows]
    latencies = sorted(float(row.latency_ms) for row in rows if row.latency_ms is not None
                       and math.isfinite(row.latency_ms) and row.latency_ms >= 0)
    return dict(total_requests=len(rows), uv=len({row.user_id for row in rows if row.user_id is not None}),
                faq_hits=sum(row.source_mode == 'faq' and row.status in ('complete', 'completed', 'success') for row in rows),
                covered=sum(bool(row.sources or row.allowed) or
                            (row.source_mode == 'faq' and row.faq_release_id is not None) for row in rows),
                permission_blocked=sum(bool(row.blocked) for row in rows),
                token_total=sum(value for value in token_values if value is not None),
                token_unknown=sum(value is None for value in token_values), latencies=latencies,
                failures=sum(row.status in ('error', 'failed', 'cancelled') for row in rows))


def percentile(values: list[float], quantile: float) -> float | None:
    """用线性插值计算真实延时分位数，空样本返回未知。"""
    if not values:
        return None
    index = (len(values) - 1) * quantile
    low, high = math.floor(index), math.ceil(index)
    return values[low] + (values[high] - values[low]) * (index - low)


def metrics_view(session: Session, actor: User, days: int) -> dict:
    """运营数值来自同一真实请求集，文字排行另受当前来源权限和部门作用域限制。"""
    require(actor, 'metrics:read')
    now = utc_now()
    start = metrics_start(days, now)
    rows = list(session.scalars(select(QAAudit).where(QAAudit.valid.is_(True),
                         QAAudit.created_at >= start, QAAudit.created_at <= now)))
    summary = summarize_audits(rows)
    questions, knowledge_counts = Counter(), Counter()
    titles = {}
    for row in rows:
        if not (row.user_id == actor.id or (actor.department_id is not None and
                                           row.department_id == actor.department_id)):
            continue
        refs = row.sources or []
        sources = authorized_sources(session, actor, refs) if refs else []
        # 曾有受限候选的问题不能从排行榜旁路推断。
        if row.blocked or (refs and not sources):
            continue
        questions[row.question] += 1
        for source in {source['knowledge_id']: source for source in sources}.values():
            knowledge_counts[source['knowledge_id']] += 1
            titles[source['knowledge_id']] = source['title']
    daily = []
    for offset in range(days):
        day = start.date() + timedelta(days=offset)
        subset = [row for row in rows if row.created_at.date() == day]
        values = summarize_audits(subset)
        daily.append(dict(date=day.isoformat(), pv=values['total_requests'], uv=values['uv'],
                          tokens=values['token_total'], latency_ms=(sum(values['latencies']) /
                          len(values['latencies']) if values['latencies'] else None)))
    count, latencies = summary['total_requests'], summary['latencies']
    return dict(pv=count, uv=summary['uv'],
                knowledge_count=sum(readable(actor, item) for item in session.scalars(select(Knowledge))),
                faq_hit_rate=summary['faq_hits'] / count if count else 0,
                coverage_rate=summary['covered'] / count if count else 0,
                permission_block_rate=summary['permission_blocked'] / count if count else 0,
                token_total=summary['token_total'], token_unknown_requests=summary['token_unknown'],
                failures=summary['failures'], latency=dict(mean_ms=sum(latencies) / len(latencies) if latencies else None,
                p50_ms=percentile(latencies, .50), p95_ms=percentile(latencies, .95)),
                top_questions=[dict(question=question, count=count) for question, count in questions.most_common(10)],
                top_knowledge=[dict(id=key, title=titles[key], count=count) for key, count in knowledge_counts.most_common(10)],
                daily=daily, as_of=now)
