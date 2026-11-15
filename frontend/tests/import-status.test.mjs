/** UUID任务链接必须保持原样，否则任务详情和自动进度刷新会失效。 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { taskIdFromQuery } from '../src/importStatus.ts'

test('UUID导入任务可从查询参数选中，非任务编号不触发详情请求', () => {
  const id = '123e4567-e89b-12d3-a456-426614174000'
  assert.equal(taskIdFromQuery(id), id)
  assert.equal(taskIdFromQuery('42'), undefined)
  assert.equal(taskIdFromQuery(''), undefined)
  assert.equal(taskIdFromQuery([id]), undefined)
})
