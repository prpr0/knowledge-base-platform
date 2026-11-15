"""通过HTTP验证真实上传的路径边界、任务持久化与仅失败项重试。"""
import json
from backend.app.models import ImportTask


def upload(api, paths, headers=None):
    """以真实multipart上传临时文本，不连接外部模型或队列。"""
    files = [('files', (path.split('/')[-1], '真实上传正文。'.encode(), 'text/plain')) for path in paths]
    return api['client'].post('/api/imports', headers=headers or api['admin'], data={'paths': json.dumps(paths), 'acl': '[]'}, files=files)


def test_upload_persists_relative_paths_and_initial_task(api):
    """合法目录批量上传写入PG权威任务结构，正文尚未发布。"""
    response = upload(api, ['技术/Python.txt', '运维/集群.txt'])
    assert response.status_code == 202
    task = response.json()
    assert task['status'] == 'queued' and task['total'] == 2
    assert {item['relative_path'] for item in task['items']} == {'技术/Python.txt', '运维/集群.txt'}
    assert all(item['status'] == 'queued' and item['attempt'] == 1 for item in task['items'])
    detail = api['client'].get('/api/imports/' + task['id'], headers=api['admin'])
    assert detail.status_code == 200 and detail.json()['id'] == task['id']


def test_upload_rejects_traversal_and_unprivileged_user(api):
    """客户端不能通过路径或角色参数越过文件与管理边界。"""
    assert upload(api, ['../outside.txt']).status_code == 422
    assert upload(api, ['safe.txt'], headers=api['reader']).status_code == 403
    assert api['client'].get('/api/imports').status_code == 401


def test_retry_only_failed_items_preserves_successful_publication(api):
    """重试不重复成功子项，失败项attempt独立增加。"""
    response = upload(api, ['成功.txt', '失败.txt'])
    assert response.status_code == 202
    task_id = response.json()['id']
    with api['app'].state.sessions() as session:
        task = session.get(ImportTask, task_id)
        task.status = 'partial'
        first, second = sorted(task.items, key=lambda item: item.relative_path)
        first.status = 'succeeded'; first.stage = 'done'; first.progress = 100
        second.status = 'failed'; second.error = '文件损坏'; second.progress = 20
        good_id, failed_id = first.id, second.id
        session.commit()
    retried = api['client'].post('/api/imports/' + task_id + '/retry', headers=api['admin'])
    assert retried.status_code == 202
    items = {item['id']: item for item in retried.json()['items']}
    assert items[good_id]['status'] == 'succeeded' and items[good_id]['attempt'] == 1
    assert items[failed_id]['status'] == 'queued' and items[failed_id]['attempt'] == 2


def test_queue_failure_is_visible_and_retryable(api):
    """发送队列失败必须持久化失败状态，不能伪报导入完成。"""
    def unavailable(task_id):
        """仅隔离验证队列故障的可见契约，不作为模型或worker验证。"""
        raise ConnectionError('测试队列不可用')
    api['app'].state.enqueue_import = unavailable
    response = upload(api, ['需要重试.txt'])
    assert response.status_code == 503
    tasks = api['client'].get('/api/imports', headers=api['admin']).json()
    assert tasks[0]['status'] == 'failed'
    assert tasks[0]['items'][0]['status'] == 'failed'
