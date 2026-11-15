/** 将数据库任务状态和解析阶段转换为控制台中文说明。 */
import type { ImportStatus } from './types'

/** 保留未知服务端状态原文，避免把未知状态显示为成功。 */
export function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = { queued: '排队中', running: '处理中', succeeded: '已成功', partial: '部分成功', failed: '失败' }
  return labels[status] ?? status
}

/** 仅服务端报告排队或运行时继续自动刷新。 */
export function taskIsPending(status: ImportStatus): boolean { return status === 'queued' || status === 'running' }

/** 显示实际阶段，不使用前端时间估算进度。 */
export function taskStageLabel(stage: string): string {
  const labels: Record<string, string> = { queued: '等待处理', parsing: '解析文档', converting: '转换文档', chunking: '切分片段', embedding: '生成向量', indexing: '建立索引', publishing: '发布版本', done: '完成', completed: '完成', succeeded: '完成', failed: '失败' }
  return labels[stage] ?? stage
}

/** 从路由读取原样UUID任务号，避免数字强制转换使进度轮询失去目标。 */
export function taskIdFromQuery(value: unknown): string | undefined {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) ? value : undefined
}
