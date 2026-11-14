"""验证未知账号返回统一认证错误，避免泄漏账号存在性。"""

def test_unknown_account_is_rejected(api):
    """不存在的账号必须由登录处理器明确拒绝。"""
    response = api['client'].post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong-password'})
    assert response.status_code == 401
