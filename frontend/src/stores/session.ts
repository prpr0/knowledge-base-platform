/** 管理内存登录会话与最新身份刷新。 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, handleUnauthorized, setAccessToken } from '../api'
import { hasPermission } from '../permissions'
import type { CurrentUser } from '../types'

/** 保存内存会话，所有菜单依据服务端最新权限计算。 */
export const useSessionStore = defineStore('session', () => {
  const user = ref<CurrentUser | null>(null)
  const expired = ref(false)
  const authenticated = computed(() => user.value !== null)
  let revision = 0
  let pending: Promise<CurrentUser | null> | null = null

  /** 注销或认证失败时同步清除身份及令牌。 */
  function clear(isExpired = false): void {
    revision += 1
    user.value = null
    pending = null
    expired.value = isExpired
    setAccessToken('')
  }
  handleUnauthorized(() => clear(true))

  /** 合并同时发生的身份刷新，避免旧请求覆盖退出后的会话。 */
  function refresh(): Promise<CurrentUser | null> {
    if (pending) return pending
    const currentRevision = revision
    const operation = api.me().then((value) => {
      if (currentRevision !== revision) return null
      user.value = value
      return value
    }).finally(() => { if (pending === operation) pending = null })
    pending = operation
    return operation
  }

  /** 登录后立即读取真实身份，不从令牌或表单推测权限。 */
  async function login(username: string, password: string): Promise<void> {
    clear()
    try {
      const result = await api.login(username, password)
      setAccessToken(result.access_token)
      await refresh()
      expired.value = false
    } catch (error) {
      clear()
      throw error
    }
  }

  /** 显式匹配单个权限。 */
  function can(permission: string): boolean { return hasPermission(user.value?.permissions ?? [], permission) }
  /** 检查菜单允许的任一真实权限。 */
  function canAny(permissions: readonly string[]): boolean { return permissions.some(can) }
  return { user, expired, authenticated, refresh, login, clear, can, canAny }
})
