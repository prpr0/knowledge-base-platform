/** 按当前组织权限读取四维 ACL 候选，禁止无权请求组织名册。 */
import { computed, ref } from 'vue'
import { api } from '../api'
import { useSessionStore } from '../stores/session'
import type { Department, Role, User } from '../types'

/** 为导入和知识维护复用同一候选读取规则。 */
export function useAclCandidates() {
  const session = useSessionStore()
  const departments = ref<Department[]>([])
  const roles = ref<Role[]>([])
  const users = ref<User[]>([])
  const canReadDepartments = computed(() => session.canAny(['organization:read', 'department:manage']))
  const canReadRoles = computed(() => session.canAny(['organization:read', 'role:manage']))
  const canReadUsers = computed(() => session.canAny(['organization:read', 'user:manage']))

  /** 清空旧候选后仅请求当前身份有权读取的名册。 */
  async function loadCandidates(): Promise<void> {
    departments.value = []; roles.value = []; users.value = []
    const manager = session.can('knowledge:manage')
    const result = await Promise.all([
      manager && canReadDepartments.value ? api.departments() : Promise.resolve([]),
      manager && canReadRoles.value ? api.roles() : Promise.resolve([]),
      manager && canReadUsers.value ? api.users() : Promise.resolve([]),
    ])
    if (!session.authenticated) return
    departments.value = result[0]; roles.value = result[1]; users.value = result[2]
  }
  return { departments, roles, users, canReadDepartments, canReadRoles, canReadUsers, loadCandidates }
}
