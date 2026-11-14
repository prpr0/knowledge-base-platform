/** 统一管理页面加载、错误、保存与删除确认状态。 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSessionStore } from '../stores/session'

/** 为真实数据页面提供加载、失败、确认与操作后的身份刷新。 */
export function usePageState(readData: () => Promise<void>) {
  const session = useSessionStore()
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  /** 读取前刷新身份，避免沿用旧角色的界面。 */
  async function reload(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      await session.refresh()
      if (session.authenticated) await readData()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : '加载失败，请重试'
    } finally { loading.value = false }
  }

  /** 成功后重新加载；失败时保留表单并明确展示服务端原因。 */
  async function mutate(action: () => Promise<unknown>, onSuccess?: () => void, message = '已保存'): Promise<void> {
    if (saving.value) return
    saving.value = true
    try {
      await action()
      onSuccess?.()
      ElMessage.success(message)
      await reload()
    } catch (cause) {
      ElMessage.error(cause instanceof Error ? cause.message : '操作失败，请重试')
      try { await session.refresh() } catch { /* 401 已由会话仓库清理，其余错误保留本次操作提示。 */ }
    } finally { saving.value = false }
  }

  /** 删除与账号状态变更必须先得到用户明确确认。 */
  async function confirm(message: string, title = '请确认操作'): Promise<boolean> {
    try { await ElMessageBox.confirm(message, title, { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }); return true }
    catch { return false }
  }
  return { loading, saving, error, reload, mutate, confirm }
}
