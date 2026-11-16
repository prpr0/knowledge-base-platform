"""使用真实Ragas评审生成逐例、可审计且不泄露正文的质量报告。"""

import asyncio
import json
import math
import os
from importlib.metadata import PackageNotFoundError, version
from numbers import Real
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

METRICS = ('faithfulness', 'context_precision', 'context_recall', 'factual_correctness', 'answer_accuracy')


def _score_number(value):
    """严格接受零到一之间的有限数值，拒绝布尔值和数字字符串。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError('invalid_metric_score')
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('invalid_metric_score')
    return value


def _requested_metrics(names):
    """仅允许显式且不重复的官方指标，报告保留本次请求范围。"""
    selected = tuple(names)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(METRICS):
        raise ValueError('invalid_metric_selection')
    return selected


def summarize_scores(rows, metric_names=METRICS) -> dict:
    """保留失败分母，分别汇总实际得到的有效指标而不宣称质量通过。"""
    metric_names = _requested_metrics(metric_names)
    rows = list(rows)
    if not rows:
        raise ValueError('empty_evaluation')
    values = {name: [] for name in metric_names}
    scored = 0
    for row in rows:
        if row.get('status') not in ('complete', 'error'):
            raise ValueError('invalid_score_status')
        scores = row.get('scores', {})
        if not isinstance(scores, dict) or set(scores) - set(metric_names):
            raise ValueError('invalid_metric_fields')
        if row['status'] == 'complete':
            if set(scores) != set(metric_names):
                raise ValueError('missing_metric_score')
            scored += 1
        for name, value in scores.items():
            values[name].append(_score_number(value))
    return {
        'total': len(rows), 'scored': scored, 'failed': len(rows) - scored,
        'status': 'complete' if scored == len(rows) else 'error',
        'requested_metrics': list(metric_names),
        'metrics': {name: {'mean': math.fsum(items) / len(items) if items else None,
                           'count': len(items)} for name, items in values.items()},
    }


def _failure_category(error):
    """仅输出固定错误类别，避免供应商异常携带凭证或响应正文。"""
    name = type(error).__name__
    if name in ('AuthenticationError', 'PermissionDeniedError'):
        return 'judge_authentication_failed'
    if name in ('RateLimitError',):
        return 'judge_rate_limited'
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or name == 'APITimeoutError':
        return 'judge_timeout'
    if name in ('APIConnectionError', 'InternalServerError', 'APIStatusError'):
        return 'judge_provider_error'
    if name in ('ValidationError', 'InstructorRetryException', 'JSONDecodeError'):
        return 'judge_structured_output_failed'
    return 'judge_evaluation_failed'


def _package_version(package):
    """记录已安装依赖版本，缺少依赖时如实标记。"""
    try:
        return version(package)
    except PackageNotFoundError:
        return None


async def score_samples(samples, output_dir: Path, *, base_url, model, api_key,
                        concurrency=2, disable_thinking=False, metric_names=METRICS) -> dict:
    """限制并发调用请求的官方Ragas指标并独占创建逐例及汇总报告。"""
    metric_names = _requested_metrics(metric_names)
    samples = list(samples)
    if not samples:
        raise ValueError('empty_evaluation')
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError('invalid_concurrency')
    endpoint = urlsplit(base_url)
    if endpoint.scheme not in ('http', 'https') or not endpoint.hostname:
        raise ValueError('invalid_judge_base_url')
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('judge_base_url_must_not_contain_credentials_or_query')
    if not isinstance(model, str) or not model.strip() or not api_key:
        raise ValueError('missing_judge_configuration')
    clean_url = urlunsplit(endpoint)
    judge = {
        'model': model, 'base_url': clean_url,
        'versions': {name: _package_version(name) for name in ('ragas', 'instructor', 'openai')},
        'disable_thinking': disable_thinking,
        'same_model_bias': '生成与评审可能使用同一模型，存在自评偏差；分数不代表独立人工结论。',
        'quality_gate': '未设质量通过阈值；complete仅表示全部指标技术执行成功。',
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scores_path = output_dir / 'scores.jsonl'
    summary_path = output_dir / 'summary.json'
    if scores_path.exists() or summary_path.exists():
        raise FileExistsError('evaluation_report_already_exists')
    # 独占创建避免检查后覆盖其他进程刚产生的报告。
    with scores_path.open('x', encoding='utf-8') as scores_file:
        with summary_path.open('x', encoding='utf-8') as summary_file:
            rows = [None] * len(samples)
            client = None
            metrics = None
            setup_failure = None
            if any(sample.get('status') == 'complete' for sample in samples):
                try:
                    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
                    from openai import AsyncOpenAI
                    import instructor
                    from ragas.llms import llm_factory
                    from ragas.metrics.collections import (
                        Faithfulness, ContextPrecision, ContextRecall, FactualCorrectness, AnswerAccuracy,
                    )
                    if judge['versions']['ragas'] != '0.4.4':
                        raise RuntimeError('unsupported_ragas_version')
                    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=120.0)
                    options = {'max_tokens': 8192}
                    if disable_thinking:
                        # DeepSeek官方要求OpenAI SDK把thinking放进extra_body。
                        options['extra_body'] = {'thinking': {'type': 'disabled'}}
                    llm = llm_factory(model, client=client, mode=instructor.Mode.JSON, **options)
                    constructors = {'faithfulness': Faithfulness, 'context_precision': ContextPrecision,
                        'context_recall': ContextRecall, 'factual_correctness': FactualCorrectness,
                        'answer_accuracy': AnswerAccuracy}
                    metrics = {name: constructors[name](llm=llm) for name in metric_names}
                except Exception:
                    setup_failure = 'judge_initialization_failed'

            pending = iter(enumerate(samples))

            async def process_worker():
                """每个工作协程顺序执行一例的指标，限制实际评审请求并发。"""
                for index, sample in pending:
                    row = {'id': sample.get('id'), 'status': 'error', 'scores': {},
                           'metric_status': {}, 'judge': judge}
                    if sample.get('status') != 'complete':
                        row['error'] = 'input_failed'
                    elif not all(isinstance(sample.get(key), str) and sample[key].strip()
                                 for key in ('user_input', 'response', 'reference')):
                        row['error'] = 'invalid_sample'
                    elif not isinstance(sample.get('retrieved_contexts'), list) or not sample['retrieved_contexts'] or not all(
                        isinstance(context, str) and context.strip() for context in sample['retrieved_contexts']
                    ):
                        row['error'] = 'invalid_retrieved_contexts'
                    elif setup_failure:
                        row['error'] = setup_failure
                    else:
                        inputs = {
                            'faithfulness': {key: sample[key] for key in ('user_input', 'response', 'retrieved_contexts')},
                            'context_precision': {key: sample[key] for key in ('user_input', 'reference', 'retrieved_contexts')},
                            'context_recall': {key: sample[key] for key in ('user_input', 'reference', 'retrieved_contexts')},
                            'factual_correctness': {key: sample[key] for key in ('response', 'reference')},
                            'answer_accuracy': {key: sample[key] for key in ('user_input', 'response', 'reference')},
                        }
                        for name in metric_names:
                            try:
                                result = await metrics[name].ascore(**inputs[name])
                                try:
                                    value = _score_number(result.value)
                                except ValueError:
                                    row['metric_status'][name] = {'status': 'error', 'error': 'invalid_metric_score'}
                                    continue
                                row['scores'][name] = value
                                row['metric_status'][name] = {'status': 'complete'}
                            except Exception as error:
                                row['metric_status'][name] = {'status': 'error', 'error': _failure_category(error)}
                        if len(row['scores']) == len(metric_names):
                            row['status'] = 'complete'
                        else:
                            row['error'] = 'metric_evaluation_failed'
                    for name in metric_names:
                        row['metric_status'].setdefault(name, {'status': 'error', 'error': row.get('error', 'metric_evaluation_failed')})
                    rows[index] = row
                    scores_file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                    scores_file.flush()

            try:
                await asyncio.gather(*(process_worker() for _ in range(min(concurrency, len(samples)))))
            finally:
                if client is not None:
                    await client.close()
            summary = summarize_scores(rows, metric_names=metric_names)
            summary['judge'] = judge
            summary_file.write(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return summary
