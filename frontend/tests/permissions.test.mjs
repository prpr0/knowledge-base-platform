/** 检查菜单按钮的准确授权，不将管理标记当通配符。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { hasPermission } from '../src/permissions.ts'

test('显式授权的按钮可用，未授权按钮不可用', () => {
  assert.equal(hasPermission(['user:manage'], 'user:manage'), true)
  assert.equal(hasPermission(['organization:read'], 'user:manage'), false)
  assert.equal(hasPermission([], 'knowledge:read'), false)
  assert.equal(hasPermission(['admin'], 'knowledge:manage'), false)
})
