"""用真实BGE挖掘高频问题，并从真实问答日志生成每日数值快照。"""
from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json
import logging
import math
from sqlalchemy import select
from .database import make_engine, session_factory
from .evidence import authorized_sources, public_citations
from .models import FAQCandidate, MetricSnapshot, MiningRun, QAAudit, User
from .operations import normalize_question, question_key, summarize_audits, utc_now
from .runtime_models import runtime_models
from .services import require

logger = logging.getLogger(__name__)


def mine_candidates(session, settings, actor, rows):
    """同部门和相同证据版本内做真实语义聚类，五次才产生待审候选。"""
    groups = defaultdict(list)
    for row in rows:
        if not row.sources or not (row.user_id == actor.id or actor.department_id is not None and row.department_id == actor.department_id):
            continue
        sources = authorized_sources(session, actor, row.sources)
        if not sources:
            continue
        source_key = tuple(sorted((ref['knowledge_id'], ref['version_id'], ref['chunk_id']) for ref in sources))
        scope = ('department', row.department_id) if row.department_id is not None else ('user', row.user_id)
        groups[(scope, source_key)].append((row, sources))
    produced = 0
    for (scope, source_key), members in groups.items():
        if len(members) < 5:
            continue
        unique = list(dict.fromkeys(normalize_question(row.question) for row, _ in members))
        vectors = runtime_models(settings).embed(unique)
        lookup = dict(zip(unique, vectors))
        clusters = []
        for row, sources in members:
            vector = lookup[normalize_question(row.question)]
            match = next((cluster for cluster in clusters if sum(a * b for a, b in zip(cluster[0][2], vector)) >= settings.faq_cluster_similarity), None)
            if match is None:
                match = []
                clusters.append(match)
            match.append((row, sources, vector))
        for cluster in clusters:
            if len(cluster) < 5:
                continue
            question = normalize_question(cluster[0][0].question)
            aliases = list(dict.fromkeys(normalize_question(row.question) for row, _, _ in cluster))[1:]
            refs = [dict(ref, label=f'S{index}') for index, ref in enumerate(cluster[0][1], 1)]
            key = sha256(json.dumps([scope, source_key, sorted([question, *aliases])], ensure_ascii=False).encode()).hexdigest()
            existing = session.scalar(select(FAQCandidate).where(FAQCandidate.cluster_key == key).with_for_update())
            if existing:
                if existing.status == 'pending':
                    existing.count = len(cluster)
                continue
            draft = '资料摘录（待人工审核与整理）：\n\n' + '\n\n'.join(f"{ref['content']} [{ref['label']}]" for ref in refs)
            similarity = min(sum(a * b for a, b in zip(cluster[0][2], vector)) for _, _, vector in cluster)
            session.add(FAQCandidate(question=question, question_key=question_key(question), answer=draft, aliases=aliases,
                sources=public_citations(refs), count=len(cluster), department_id=cluster[0][0].department_id,
                created_by_id=actor.id,
                confidence=max(-1., min(1., similarity)), cluster_key=key))
            produced += 1
    session.flush()
    return produced


def run_mining(settings, run_id):
    """领取一次真实队列任务，失败保留终态及安全错误说明。"""
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    try:
        with sessions() as session:
            run = session.get(MiningRun, run_id, with_for_update=True)
            if run is None or run.status != 'queued':
                return
            run.status = 'running'
            session.commit()
            logger.info('流程=FAQ挖掘 步骤=开始 run_id=%s', run_id)
            try:
                actor = session.get(User, run.requested_by)
                if actor is None or not actor.is_active:
                    raise ValueError('发起人已失效')
                require(actor, 'faq:manage')
                rows = list(session.scalars(select(QAAudit).where(QAAudit.valid.is_(True), QAAudit.created_at >= utc_now() - timedelta(days=7))))
                run.count = mine_candidates(session, settings, actor, rows)
                run.status, run.finished_at = 'succeeded', utc_now()
                session.commit()
                logger.info('流程=FAQ挖掘 状态=成功 run_id=%s candidates=%s', run_id, run.count)
            except Exception as exc:
                session.rollback()
                run = session.get(MiningRun, run_id)
                run.status, run.error, run.finished_at = 'failed', '挖掘失败：' + type(exc).__name__, utc_now()
                session.commit()
                logger.error('流程=FAQ挖掘 状态=失败 run_id=%s error_type=%s', run_id, type(exc).__name__)
    finally:
        engine.dispose()


def aggregate_metrics(settings):
    """以统一计数规则更新UTC日快照，API与worker可对同一日志集对账。"""
    engine = make_engine(settings.database_url)
    sessions = session_factory(engine)
    try:
        with sessions() as session:
            groups = defaultdict(list)
            for audit in session.scalars(select(QAAudit).where(QAAudit.valid.is_(True))):
                groups[audit.created_at.date()].append(audit)
            for day, rows in groups.items():
                values = summarize_audits(rows)
                values.pop('failures')
                snapshot = session.get(MetricSnapshot, day)
                if snapshot is None:
                    snapshot = MetricSnapshot(day=day)
                    session.add(snapshot)
                for key, value in values.items():
                    setattr(snapshot, key, value)
                snapshot.as_of = utc_now()
            session.commit()
            logger.info('流程=指标聚合 状态=成功 days=%s', len(groups))
    finally:
        engine.dispose()
