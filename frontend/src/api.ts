/** 封装真实API请求、错误转换与内存令牌传递。 */
import type { AclEntry, CurrentUser, Department, KnowledgeCreate, KnowledgeDocument, KnowledgeManagedMetadata, KnowledgeMetadata, KnowledgePatch, Permission, Role, User, UserWrite } from './types'

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

/** 所有业务请求均经过真实 API；401 立即销毁内存会话。 */
async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const requestToken = accessToken
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (requestToken) headers.Authorization = 'Bearer ' + requestToken
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch('/api' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', credentials: 'omit' })
  } catch {
    throw new ApiError(0, '无法连接服务，请检查 API 服务与网络后重试')
  }
  if (response.status === 401 && requestToken === accessToken) { accessToken = ''; onUnauthorized() }
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined
    const message = detail === undefined ? '请求失败（HTTP ' + response.status + '）' : detailMessage(detail)
    throw new ApiError(response.status, response.status === 403 ? '权限不足：' + message : response.status === 401 ? '身份验证失败：' + message : message)
  }
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
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
}
