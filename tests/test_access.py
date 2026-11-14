"""验证四维OR、默认拒绝和直属部门边界。"""
import pytest
from backend.app.access import Grant, Principal, can_read

@pytest.mark.parametrize('grants,expected', [
    ([], False), ([Grant('global')], True),
    ([Grant('department', 20)], True), ([Grant('department', 10)], False),
    ([Grant('role', 3)], True), ([Grant('role', 4)], False),
    ([Grant('user', 7)], True), ([Grant('user', 8)], False),
    ([Grant('department', 10), Grant('role', 4), Grant('user', 7)], True),
    ([Grant('unknown', 7)], False), ([Grant('global', 99)], False),
])
def test_current_identity_grants(grants, expected):
    """只依据直属部门、实际角色或本人匹配任一有效授权。"""
    principal = Principal(7, 20, frozenset({3}))
    assert can_read(principal, grants) is expected
