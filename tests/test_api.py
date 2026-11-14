"""通过真实HTTP行为验证身份、组织操作和正文权限隔离。"""
import pytest

def test_login_and_identity(api):
    """正确密码登录且返回当前权限，错误密码统一拒绝。"""
    client = api["client"]
    response = client.post('/api/auth/login', json={"username": "reader", "password": "test-password-only"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    result = client.get('/api/auth/me', headers={"Authorization": "Bearer " + token})
    assert result.status_code == 200
    assert result.json()["permissions"] == ["knowledge:read"]
    assert "password_hash" not in result.json()
    assert client.post('/api/auth/login', json={"username": "reader", "password": "bad"}).status_code == 401
    assert client.get('/api/auth/me', headers={"Authorization": "Bearer " + token + "changed"}).status_code == 401
    assert client.get('/api/users').status_code == 401

def test_menu_button_permissions_enforced_server_side(api):
    """读者直接构造管理请求依然被拒绝，客户端字段无法提权。"""
    client = api["client"]
    assert client.post('/api/departments', headers=api['reader'], json={"name": "伪造部门"}).status_code == 403
    assert client.patch(f'/api/users/{api["ids"]["reader"]}', headers=api['reader'], json={"role_ids": [api['ids']['admin_role']]}).status_code == 403
    assert client.get('/api/users', headers=api['reader']).status_code == 403
    assert client.get('/api/permissions', headers=api['admin']).status_code == 200

def test_role_change_affects_existing_token(api):
    """角色变更后旧令牌立即使用新权限集合。"""
    client, role = api['client'], api['ids']['reader_role']
    assert client.get('/api/users', headers=api['reader']).status_code == 403
    response = client.patch(f'/api/roles/{role}', headers=api['admin'], json={"permissions": ["knowledge:read", "organization:read"]})
    assert response.status_code == 200
    assert client.get('/api/users', headers=api['reader']).status_code == 200
    assert client.get('/api/auth/me', headers=api['reader']).json()['permission_version'] > 1
    assert client.patch(f'/api/roles/{role}', headers=api['admin'], json={"permissions": ["knowledge:read"]}).status_code == 200
    assert client.get('/api/users', headers=api['reader']).status_code == 403

def test_department_tree_cycles_and_referenced_delete(api):
    """部门层级不能形成环，有成员或子部门时不能删除。"""
    client, ids = api['client'], api['ids']
    assert client.patch(f'/api/departments/{ids["parent"]}', headers=api['admin'], json={"parent_id": ids['department']}).status_code == 409
    assert client.delete(f'/api/departments/{ids["parent"]}', headers=api['admin']).status_code == 409
    assert client.delete(f'/api/departments/{ids["department"]}', headers=api['admin']).status_code == 409
    assert client.post('/api/departments', headers=api['admin'], json={"name": "技术部"}).status_code == 409

@pytest.mark.parametrize('field,value', [('is_active', False), ('role_ids', [])])
def test_last_administrator_cannot_be_removed(api, field, value):
    """拒绝会使所有账号失去组织维护能力的用户变更。"""
    response = api['client'].patch(f'/api/users/{api["ids"]["admin"]}', headers=api['admin'], json={field: value})
    assert response.status_code == 409
    assert api['client'].get('/api/users', headers=api['admin']).status_code == 200

def test_last_admin_role_protected(api):
    """删除或清空最后管理员角色必须事务回滚。"""
    path = f'/api/roles/{api["ids"]["admin_role"]}'
    assert api['client'].patch(path, headers=api['admin'], json={"permissions": []}).status_code == 409
    assert api['client'].delete(path, headers=api['admin']).status_code == 409

def test_knowledge_default_deny_and_explicit_acl(api):
    """管理权限可建知识但不隐式拥有正文读取权。"""
    client = api['client']
    response = client.post('/api/knowledge', headers=api['admin'], json={"title": "测试资料", "content": "不能默认公开的内容"})
    assert response.status_code == 201
    item = response.json()
    assert 'content' not in item
    path = f'/api/knowledge/{item["id"]}'
    assert client.get(path, headers=api['admin']).status_code == 403
    assert client.get(path, headers=api['reader']).status_code == 403
    assert client.get('/api/knowledge', headers=api['reader']).json() == []
    assert client.put(path + '/acl', headers=api['admin'], json={"acl": [{"type": "global", "entity_id": None}]}).status_code == 200
    assert client.get(path, headers=api['reader']).json()['content'] == '不能默认公开的内容'
    assert client.get('/api/knowledge/manage', headers=api['reader']).status_code == 403

def test_department_or_acl_and_immediate_revoke(api):
    """部门OR个人允许，直属部门变更与撤权立即影响同一令牌。"""
    client, ids = api['client'], api['ids']
    record = client.post('/api/knowledge', headers=api['admin'], json={"title": "运维", "content": "运维正文", "acl": [{"type": "department", "entity_id": ids['parent']}, {"type": "user", "entity_id": ids['reader']}]}).json()
    path = f'/api/knowledge/{record["id"]}'
    assert client.get(path, headers=api['reader']).status_code == 200
    assert client.put(path + '/acl', headers=api['admin'], json={"acl": [{"type": "department", "entity_id": ids['parent']}]}).status_code == 200
    assert client.get(path, headers=api['reader']).status_code == 403
    assert client.patch(f'/api/users/{ids["reader"]}', headers=api['admin'], json={"department_id": ids['parent']}).status_code == 200
    assert client.get(path, headers=api['reader']).status_code == 200
    assert client.patch(f'/api/users/{ids["reader"]}', headers=api['admin'], json={"is_active": False}).status_code == 200
    assert client.get(path, headers=api['reader']).status_code == 401

def test_password_reset_revokes_existing_token(api):
    """密码更新撤销旧令牌，新的密码仍可登录。"""
    client = api['client']
    response = client.patch(f'/api/users/{api["ids"]["reader"]}', headers=api['admin'], json={"password": "new-test-password"})
    assert response.status_code == 200
    assert client.get('/api/auth/me', headers=api['reader']).status_code == 401
    assert client.post('/api/auth/login', json={"username": "reader", "password": "new-test-password"}).status_code == 200

def test_invalid_references_and_null_required_fields(api):
    """拒绝未知实体、伪权限码和会破坏必填列的null输入。"""
    client = api['client']
    assert client.post('/api/users', headers=api['admin'], json={"username": "new", "display_name": "新用户", "password": "test-password", "role_ids": [99999]}).status_code == 422
    assert client.post('/api/roles', headers=api['admin'], json={"name": "无效权限", "permissions": ["*"]}).status_code == 422
    assert client.patch(f'/api/users/{api["ids"]["reader"]}', headers=api['admin'], json={"display_name": None}).status_code == 422
    assert client.post('/api/knowledge', headers=api['admin'], json={"title": "坏ACL", "content": "正文", "acl": [{"type": "role", "entity_id": 99999}]}).status_code == 422

def test_crud_and_deleted_entity_grants_are_removed(api):
    """真实管理删除关联授权，防止实体编号重用导致旧ACL复活。"""
    client = api['client']
    role_response = client.post('/api/roles', headers=api['admin'], json={"name": "临时角色", "permissions": ["knowledge:read"]})
    assert role_response.status_code == 201
    role = role_response.json()
    knowledge = client.post('/api/knowledge', headers=api['admin'], json={"title": "临时资料", "content": "正文", "acl": [{"type": "role", "entity_id": role['id']}]}).json()
    assert client.delete(f'/api/roles/{role["id"]}', headers=api['admin']).status_code == 204
    managed = client.get('/api/knowledge/manage', headers=api['admin']).json()
    assert next(item for item in managed if item['id'] == knowledge['id'])['acl'] == []
    assert client.delete(f'/api/knowledge/{knowledge["id"]}', headers=api['admin']).status_code == 204
    assert client.get(f'/api/knowledge/{knowledge["id"]}', headers=api['admin']).status_code == 404
