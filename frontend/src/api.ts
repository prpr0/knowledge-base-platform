/** 封装真实API请求、错误转换与内存令牌传递。 */
import type { AclEntry, CurrentUser, Department, ImportTask, KnowledgeChunk, KnowledgeCreate, KnowledgeDocument, KnowledgeDownload, KnowledgeManagedMetadata, KnowledgeMetadata, KnowledgePatch, KnowledgeVersion, Permission, Role, SearchResponse, User, UserWrite } from './types'

let accessToken = ''
let onUnauthorized: () => void = () => {}

/** 令牌只驻留当前页面内存，刷新浏览器后必须重新登录。 */
export function setAccessToken(token: string): void { accessToken = token }

/** 由会话仓库接管失效令牌，避免 API 与路由循环依赖。 */
export function handleUnauthorized(handler: () => void): void { onUnauthorized = handler }

/** 保留HTTP状态及面向用户的错误原因。 */
export class ApiError extends Error {
  /** 创建不包含响应凭证或原始请求正文的接口错误。 */
  constructor(public readonly status: number, message: string) { super(message); this.name = 'ApiError' }
}

/** 将 FastAPI 验证错误转为可读文案，保留服务端明确的拒绝原因。 */
function detailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => {
    if (typeof item === 'object' && item !== null && 'msg' in item) {
      const error = item as { loc?: (string | number)[]; msg: string }
      return (error.loc?.slice(1).join('.') || '参数') + '：' + error.msg
    }
    return String(item)
  }).join('；')
  return '请求失败，请稍后重试'
}

/** 统一附带 Bearer，FormData 由浏览器生成包含 boundary 的请求头。 */
async function fetchResponse(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<Response> {
  const requestToken = accessToken
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (requestToken) headers.Authorization = 'Bearer ' + requestToken
  const multipart = body instanceof FormData
  if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch('/api' + path, { method, headers, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body), cache: 'no-store', credentials: 'omit', signal })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, '无法连接服务，请检查 API 服务与网络后重试')
  }
  if (response.status === 401 && requestToken === accessToken) { accessToken = ''; onUnauthorized() }
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined
    const message = detail === undefined ? '请求失败（HTTP ' + response.status + '）' : detailMessage(detail)
    throw new ApiError(response.status, response.status === 403 ? '权限不足：' + message : response.status === 401 ? '身份验证失败：' + message : message)
  }
  return response
}

/** 所有 JSON 业务请求共用认证失效与服务端错误处理。 */
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetchResponse(path, method, body)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

/** 以明确的文件与相对路径配对创建真实导入任务，默认不授予正文访问权。 */
async function createImport(files: { file: File; path: string }[], acl: AclEntry[], category: string, supplementTaskId?: number): Promise<ImportTask> {
  const data = new FormData()
  for (const entry of files) data.append('files', entry.file, entry.file.name)
  data.append('paths', JSON.stringify(files.map((entry) => entry.path)))
  data.append('acl', JSON.stringify(acl))
  if (category.trim()) data.append('category', category.trim())
  if (supplementTaskId !== undefined) data.append('supplement_task_id', String(supplementTaskId))
  return request<ImportTask>('/imports', 'POST', data)
}

/** 下载仍经过正文 ACL；401 与 JSON 请求一样立即清除会话。 */
async function downloadKnowledge(id: number): Promise<KnowledgeDownload> {
  const response = await fetchResponse('/knowledge/' + id + '/download')
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  const plain = /filename="([^"]+)"|filename=([^;]+)/i.exec(disposition)
  let filename = plain?.[1] ?? plain?.[2]?.trim() ?? null
  if (encoded) { try { filename = decodeURIComponent(encoded) } catch { /* 非法编码时保留普通文件名。 */ } }
  return { blob: await response.blob(), filename }
}

export const api = {
  login: (username: string, password: string) => request<{ access_token: string; token_type: string }>('/auth/login', 'POST', { username, password }),
  me: () => request<CurrentUser>('/auth/me'),
  departments: () => request<Department[]>('/departments'),
  createDepartment: (body: { name: string; parent_id: number | null }) => request<Department>('/departments', 'POST', body),
  updateDepartment: (id: number, body: { name: string; parent_id: number | null }) => request<Department>('/departments/' + id, 'PATCH', body),
  deleteDepartment: (id: number) => request<void>('/departments/' + id, 'DELETE'),
  users: () => request<User[]>('/users'),
  createUser: (body: UserWrite & { username: string; password: string }) => request<User>('/users', 'POST', body),
  updateUser: (id: number, body: UserWrite) => request<User>('/users/' + id, 'PATCH', body),
  deleteUser: (id: number) => request<void>('/users/' + id, 'DELETE'),
  roles: () => request<Role[]>('/roles'),
  createRole: (body: { name: string; permissions: string[] }) => request<Role>('/roles', 'POST', body),
  updateRole: (id: number, body: { name: string; permissions: string[] }) => request<Role>('/roles/' + id, 'PATCH', body),
  deleteRole: (id: number) => request<void>('/roles/' + id, 'DELETE'),
  permissions: () => request<Permission[]>('/permissions'),
  knowledge: () => request<KnowledgeMetadata[]>('/knowledge'),
  managedKnowledge: () => request<KnowledgeManagedMetadata[]>('/knowledge/manage'),
  readKnowledge: (id: number) => request<KnowledgeDocument>('/knowledge/' + id),
  createKnowledge: (body: KnowledgeCreate) => request<KnowledgeManagedMetadata>('/knowledge', 'POST', body),
  updateKnowledge: (id: number, body: KnowledgePatch) => request<KnowledgeManagedMetadata>('/knowledge/' + id, 'PATCH', body),
  setKnowledgeAcl: (id: number, acl: AclEntry[]) => request<KnowledgeManagedMetadata>('/knowledge/' + id + '/acl', 'PUT', { acl }),
  deleteKnowledge: (id: number) => request<void>('/knowledge/' + id, 'DELETE'),
  createImport,
  /** 读取当前身份可见的真实任务列表。 */
  imports: () => request<ImportTask[]>('/imports'),
  /** 读取指定导入或重建任务及其分项进度。 */
  importTask: (id: string) => request<ImportTask>('/imports/' + id),
  /** 显式重试失败分项，不重复发布成功分项。 */
  retryImport: (id: string) => request<ImportTask>('/imports/' + id + '/retry', 'POST'),
  /** 读取版本管理元数据和失败原因。 */
  knowledgeVersions: (id: number) => request<KnowledgeVersion[]>('/knowledge/' + id + '/versions'),
  /** 重新鉴权并读取当前活动版本的获准切片。 */
  knowledgeChunks: (id: number) => request<KnowledgeChunk[]>('/knowledge/' + id + '/chunks'),
  /** 以完整切片新正文创建真实版本重建任务。 */
  updateChunk: (id: number, content: string) => request<ImportTask>('/chunks/' + id, 'PATCH', { content }),
  downloadKnowledge,
  /** 检索获准片段，权限受限信息仅由后端提供布尔标记。 */
  search: (query: string, limit: number) => request<SearchResponse>('/search', 'POST', { query, limit }),
}

/** 用同一身份读取SSE，处理跨网络块的UTF-8和多行数据。 */
export async function askStream(id: number, question: string, signal: AbortSignal, consume: (name: string, data: Record<string, unknown>) => void): Promise<void> {
  const response = await fetchResponse('/conversations/' + id + '/ask', 'POST', { question }, signal)
  if (!response.body) throw new Error('浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', finished = false
  try {
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      buffer = buffer.replace(/\r\n/g, '\n')
      let end = buffer.indexOf('\n\n')
      while (end >= 0) {
        const block = buffer.slice(0, end); buffer = buffer.slice(end + 2)
        const lines = block.split('\n')
        const name = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
        if (data) consume(name, JSON.parse(data) as Record<string, unknown>)
        if (name === 'done' || name === 'error') finished = true
        end = buffer.indexOf('\n\n')
      }
      if (chunk.done) break
    }
    if (!finished) throw new Error('连接中断，未收到问答终态')
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
}
