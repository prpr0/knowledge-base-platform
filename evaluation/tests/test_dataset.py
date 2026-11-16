"""验证开源标注的确定性抽样、答案隔离和数据完整性。"""
import importlib
import json
from pathlib import Path

import pytest


def dataset_module():
    """缺少目标实现时给出明确行为红测，而非缺外部依赖错误。"""
    try:
        return importlib.import_module('evaluation.dataset')
    except ModuleNotFoundError as error:
        if error.name == 'evaluation.dataset':
            pytest.fail('尚未实现可复现的CMRC数据准备功能')
        raise


def fixture_source(tmp_path):
    """建立人工小夹具验证数据转换，不冒充真实开源数据测试。"""
    records = []
    for index in range(4):
        context = f'编号{index}的星球属于太阳系，已知卫星数量为{index + 2}颗。'
        answer = f'{index + 2}颗'
        records.append(dict(title=f'星球{index}', paragraphs=[dict(context=context, qas=[dict(id=f'q{index}', question=f'编号{index}的星球有多少卫星？', answers=[dict(text=answer, answer_start=context.index(answer))])])]))
    source = tmp_path / 'cmrc2018_dev.json'
    source.write_text(json.dumps(dict(data=records), ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'LICENCE').write_text('CC BY-SA 4.0 — unit fixture only', encoding='utf-8')
    return source


def test_prepare_is_deterministic_and_never_uploads_questions_or_answers(tmp_path):
    """只把原段落放进文档，标签不作为附加答案泄漏，固定种子可重现。"""
    module = dataset_module()
    source = fixture_source(tmp_path)
    first, second = tmp_path / 'first', tmp_path / 'second'
    manifest = module.prepare_dataset(source, first, count=3, seed=42)
    module.prepare_dataset(source, second, count=3, seed=42)
    cases = module.load_cases(first)
    assert cases == module.load_cases(second)
    assert len(cases) == len({case['document_id'] for case in cases}) == 3
    docs = {entry['document_id']: (first / entry['path']).read_text(encoding='utf-8') for entry in manifest['documents']}
    for case in cases:
        assert case['reference'] in case['reference_context']
        assert case['question'] not in docs[case['document_id']]
        assert 'reference_answers' not in docs[case['document_id']]
        assert case['reference_context'] in docs[case['document_id']]


def test_dataset_loader_rejects_modified_evidence_and_bad_answer_spans(tmp_path):
    """拒绝准备后替换语料，原标注跨度错误时也不能悄悄加入评测。"""
    module = dataset_module()
    source = fixture_source(tmp_path)
    output = tmp_path / 'prepared'
    manifest = module.prepare_dataset(source, output, count=2, seed=42)
    path = output / manifest['documents'][0]['path']
    path.write_text('被替换的内容', encoding='utf-8')
    with pytest.raises(ValueError):
        module.load_cases(output)
    payload = json.loads(source.read_text(encoding='utf-8'))
    payload['data'][0]['paragraphs'][0]['qas'][0]['answers'][0]['answer_start'] = 0
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError):
        module.prepare_dataset(source, tmp_path / 'invalid', count=4, seed=42)


def test_sampling_is_independent_of_input_order(tmp_path):
    """调整文章和问题顺序不改变相同种子的样本。"""
    module = dataset_module()
    source = fixture_source(tmp_path)
    first = tmp_path / 'first'
    module.prepare_dataset(source, first, count=3)
    payload = json.loads(source.read_text(encoding='utf-8'))
    payload['data'].reverse()
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    second = tmp_path / 'second'
    module.prepare_dataset(source, second, count=3)
    assert module.load_cases(first) == module.load_cases(second)
    assert json.loads((first / 'manifest.json').read_text(encoding='utf-8'))['source']['verified_download'] is False


def test_loader_rejects_case_changes_and_path_escape(tmp_path):
    """拒绝篡改问题及通过清单读取数据目录外的文件。"""
    module = dataset_module()
    source = fixture_source(tmp_path)
    output = tmp_path / 'prepared'
    manifest = module.prepare_dataset(source, output, count=2)
    cases = output / 'cases.jsonl'
    original = cases.read_bytes()
    cases.write_bytes(original + b'\n')
    with pytest.raises(ValueError):
        module.load_cases(output)
    cases.write_bytes(original)
    manifest['documents'][0]['path'] = '../cmrc2018_dev.json'
    (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        module.load_cases(output)


def test_download_uses_pinned_urls_and_verified_cache(tmp_path, monkeypatch):
    """模拟网络验证固定开发集 URL 和缓存摘要，不声称真实来源已验证。"""
    import io

    module = dataset_module()
    source = fixture_source(tmp_path)
    payload = source.read_bytes()
    calls = []

    def fake_open(url, timeout):
        """仅返回人工数据和许可，记录请求地址。"""
        calls.append(url)
        return io.BytesIO(payload if url == module.SOURCE_URL else b'fixture license')

    monkeypatch.setattr(module, 'urlopen', fake_open)
    downloaded = module.download_cmrc(tmp_path / 'download')
    assert calls == [module.SOURCE_URL, module.LICENSE_URL]
    assert all(module.REVISION in url for url in calls)
    assert module.download_cmrc(downloaded.parent) == downloaded
    assert len(calls) == 2
    downloaded.write_bytes(b'changed')
    with pytest.raises(ValueError):
        module.download_cmrc(downloaded.parent)
    assert downloaded.read_bytes() == b'changed'


def test_official_nonextractive_aliases_remain_reference_only(tmp_path):
    """官方-1偏移的人工改述答案可评审，但不能被追加到上传原文。"""
    module = dataset_module()
    source = fixture_source(tmp_path)
    payload = json.loads(source.read_text(encoding='utf-8'))
    for article in payload['data']:
        qa = article['paragraphs'][0]['qas'][0]
        qa['answers'].insert(0, dict(text='人工改述：卫星数量应以记录为准', answer_start=-1))
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    output = tmp_path / 'nonextractive'
    manifest = module.prepare_dataset(source, output, count=3, seed=42)
    cases = module.load_cases(output)
    assert manifest['annotations']['nonextractive_answers'] == 4
    assert all(case['reference'] == '人工改述：卫星数量应以记录为准' for case in cases)
    assert all('人工改述' not in (output / entry['path']).read_text(encoding='utf-8') for entry in manifest['documents'])
