"""验证Ragas结果不能静默忽略失败、空样本或非有限分数。"""
import importlib

import pytest


def scoring_module():
    """缺少评分功能时产生明确目标行为红测。"""
    try:
        return importlib.import_module('evaluation.scoring')
    except ModuleNotFoundError as error:
        if error.name == 'evaluation.scoring':
            pytest.fail('尚未实现可审计的Ragas指标评分功能')
        raise


def test_summary_reports_failures_and_keeps_denominator():
    """部分失败必须保留总例数和失败数，不能将均值宣称为整批通过。"""
    summarize = scoring_module().summarize_scores
    rows = [dict(id='a', status='complete', scores=dict(faithfulness=1.0, context_precision=.5, context_recall=1.0, factual_correctness=.8, answer_accuracy=.9)),
            dict(id='b', status='error', error='provider_unavailable', scores={})]
    summary = summarize(rows)
    assert summary['total'] == 2 and summary['scored'] == 1 and summary['failed'] == 1
    assert summary['status'] != 'complete'
    assert summary['metrics']['faithfulness']['count'] == 1
    assert summary['metrics']['faithfulness']['mean'] == 1.0


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -0.1, 1.1])
def test_summary_rejects_invalid_metric_numbers(bad):
    """异常分数和缺少指标不生成可误解的均值。"""
    summarize = scoring_module().summarize_scores
    with pytest.raises(ValueError):
        summarize([dict(id='x', status='complete', scores=dict(faithfulness=bad, context_precision=1., context_recall=1., factual_correctness=1., answer_accuracy=1.))])


def test_summary_rejects_empty_evaluation():
    """没有实际评测样本不能报告通过。"""
    with pytest.raises(ValueError):
        scoring_module().summarize_scores([])


@pytest.mark.parametrize('bad', [True, '0.5', None])
def test_summary_rejects_non_numeric_scores(bad):
    """布尔值、字符串和空值不能作为有效评审分数。"""
    scores = dict.fromkeys(scoring_module().METRICS, 1.)
    scores['faithfulness'] = bad
    with pytest.raises(ValueError):
        scoring_module().summarize_scores([dict(status='complete', scores=scores)])


def test_summary_rejects_missing_metric():
    """完成状态必须具有本次请求的全部真实指标。"""
    with pytest.raises(ValueError):
        scoring_module().summarize_scores([dict(status='complete', scores={'faithfulness': 1.})])


def test_summary_preserves_partial_metric_counts():
    """部分指标成功的失败例仍提供可用计数，但不增加完成例数。"""
    summary = scoring_module().summarize_scores([
        dict(status='error', scores={'faithfulness': .5}),
    ])
    assert summary['scored'] == 0 and summary['failed'] == 1
    assert summary['status'] == 'error'
    assert summary['metrics']['faithfulness'] == dict(mean=.5, count=1)
    assert summary['metrics']['context_recall'] == dict(mean=None, count=0)


def test_failed_inputs_write_reports_without_judge_dependencies(tmp_path):
    """采集失败不调用评审，也不把输入正文和异常原文写入评分报告。"""
    import asyncio
    import json

    summary = asyncio.run(scoring_module().score_samples(
        [dict(id='failed', status='error', response='private response', error='private error')],
        tmp_path, base_url='https://example.invalid/v1', model='judge', api_key='secret-key',
    ))
    assert summary['status'] == 'error' and summary['failed'] == 1
    report = (tmp_path / 'scores.jsonl').read_text(encoding='utf-8')
    row = json.loads(report)
    assert row['scores'] == {} and row['error'] == 'input_failed'
    for private in ('secret-key', 'private response', 'private error'):
        assert private not in report
        assert private not in (tmp_path / 'summary.json').read_text(encoding='utf-8')


def test_scoring_refuses_existing_report(tmp_path):
    """已有任一种报告时都拒绝产生覆盖或额外评审请求。"""
    import asyncio

    existing = tmp_path / 'summary.json'
    existing.write_text('existing', encoding='utf-8')
    with pytest.raises(FileExistsError):
        asyncio.run(scoring_module().score_samples(
            [dict(id='x', status='error')], tmp_path,
            base_url='https://example.invalid/v1', model='judge', api_key='secret-key',
        ))
    assert existing.read_text(encoding='utf-8') == 'existing'
    assert not (tmp_path / 'scores.jsonl').exists()


def test_qa_accuracy_can_be_supplemented_without_rescoring_existing_metrics():
    """新增问答准确性可单独补算，报告明确声明请求范围而不覆盖原低分。"""
    summary = scoring_module().summarize_scores([
        dict(id='qa', status='complete', scores={'answer_accuracy': 1.}),
        dict(id='failed', status='error', scores={}),
    ], metric_names=('answer_accuracy',))
    assert summary['requested_metrics'] == ['answer_accuracy']
    assert summary['metrics']['answer_accuracy'] == dict(mean=1., count=1)
    assert summary['total'] == 2 and summary['failed'] == 1 and summary['status'] == 'error'
