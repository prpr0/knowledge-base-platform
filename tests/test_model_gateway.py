"""检验发布摘要、质量门槛、独立校准和只写密钥的真实边界。"""
import json
from hashlib import sha256
import pytest
from backend.app.model_gateway import MODEL_NAME, retrieval_config_id, validate_calibration, validate_release


def release_fixture(tmp_path):
    """临时小文件仅用于验证门禁，不作为真实模型或评估产物。"""
    base = dict(citation_validity=.9, evidence_support=.95, keypoint_coverage=.90, terminology_format=.96, refusal_recall=.95, answerable_false_refusal=.04)
    tuned = dict(base, citation_validity=1., keypoint_coverage=.93)
    report = tmp_path / 'evaluation.json'
    report.write_text(json.dumps({'metrics': {'base': base, 'finetuned': tuned}}), encoding='utf-8')
    artifact = tmp_path / 'fixture.bin'
    artifact.write_bytes(b'unit-test-only')
    manifest = dict(model_name=MODEL_NAME, model_version='test-fixture', base_model='Qwen/Qwen3-4B-Instruct-2507', base_revision='test-fixture',
        artifacts=[{'path': file.name, 'sha256': sha256(file.read_bytes()).hexdigest()} for file in (report, artifact)], evaluation_report=report.name)
    path = tmp_path / 'release-manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path, manifest, artifact, report


def test_release_rejects_changed_artifacts_and_missing_report_hash(tmp_path):
    """发布后替换文件或不校验评估报告均不能通过。"""
    path, manifest, artifact, report = release_fixture(tmp_path)
    assert validate_release(path)[0] == 'test-fixture'
    artifact.write_bytes(b'changed')
    with pytest.raises(ValueError, match='摘要'):
        validate_release(path)
    manifest['artifacts'] = []
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        validate_release(path)


def test_release_rejects_regression_even_when_absolute_targets_pass(tmp_path):
    """绝对门槛通过但忠实性低于基座时仍拒绝发布。"""
    path, manifest, artifact, report = release_fixture(tmp_path)
    metrics = json.loads(report.read_text())
    metrics['metrics']['base']['evidence_support'] = .99
    report.write_text(json.dumps(metrics), encoding='utf-8')
    manifest['artifacts'][0]['sha256'] = sha256(report.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='基座'):
        validate_release(path)


def test_calibration_binds_actual_retrieval_and_rejects_invalid_counts(api, tmp_path):
    """不允许其他检索配置、负计数或超过5%的错误作答报告进入生成链路。"""
    settings = api['app'].state.settings
    value = dict(validated=True, configuration_id=retrieval_config_id(settings), threshold=.8, unanswerable_count=100,
        wrong_answer_count=5, answerable_count=100, supported_retrieval_count=90)
    path = tmp_path / 'calibration.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    assert validate_calibration(path, settings) == .8
    for changes in ({'wrong_answer_count': 6}, {'configuration_id': 'other'}, {'threshold': float('nan')}, {'supported_retrieval_count': 101}):
        path.write_text(json.dumps(dict(value, **changes)), encoding='utf-8')
        with pytest.raises(ValueError):
            validate_calibration(path, settings)


def test_configuration_does_not_echo_service_key(api):
    """缺独立加密密钥时拒绝写入且响应不包含用户提交的凭证。"""
    secret = 'unit-test-key-only'
    response = api['client'].put('/api/settings/model', headers=api['admin'], json=dict(base_url='http://127.0.0.1:8000', model_name=MODEL_NAME, api_key=secret))
    assert response.status_code == 422 and secret not in response.text
    invalid = api['client'].put('/api/settings/model', headers=api['admin'], json=dict(base_url='http://user:password@127.0.0.1:8000'))
    assert invalid.status_code == 422 and 'password' not in invalid.text
