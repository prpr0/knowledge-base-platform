<!-- 维护用户、直属部门、角色与账号状态。 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '../api'
import type { Department, Role, User, UserWrite } from '../types'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import DepartmentSelect from '../components/DepartmentSelect.vue'
import PageHeader from '../components/PageHeader.vue'
const session = useSessionStore()
const users = ref<User[]>([])
const departments = ref<Department[]>([])
const roles = ref<Role[]>([])
const query = ref('')
const dialog = ref(false)
const editingId = ref<number>()
const formRef = ref<FormInstance>()
const form = reactive({ username: '', display_name: '', password: '', department_id: null as number | null, role_ids: [] as number[], is_active: true })
const canReadDepartments = computed(() => session.canAny(['organization:read', 'department:manage']))
const canReadRoles = computed(() => session.canAny(['organization:read', 'role:manage']))
const filtered = computed(() => users.value.filter((item) => (item.username + ' ' + item.display_name).toLowerCase().includes(query.value.trim().toLowerCase())))
const rules = computed<FormRules>(() => ({
  username: [{ required: true, whitespace: true, message: '请输入用户名', trigger: 'blur' }, { pattern: /^[A-Za-z0-9_.-]+$/, message: '用户名只允许字母、数字、下划线、点或短横线', trigger: 'blur' }, { max: 80, message: '用户名最多 80 个字符', trigger: 'blur' }],
  display_name: [{ required: true, whitespace: true, message: '请输入显示名称', trigger: 'blur' }, { max: 100, message: '显示名称最多 100 个字符', trigger: 'blur' }],
  password: [{ required: editingId.value === undefined, message: '创建用户时必须设置密码', trigger: 'blur' }, { min: 8, max: 128, message: '密码须为 8 至 128 个字符', trigger: 'blur' }],
}))
const { loading, saving, error, reload, mutate, confirm } = usePageState(async () => {
  users.value = []; departments.value = []; roles.value = []
  const results = await Promise.all([
    session.canAny(['organization:read', 'user:manage']) ? api.users() : Promise.resolve([]),
    canReadDepartments.value ? api.departments() : Promise.resolve([]),
    canReadRoles.value ? api.roles() : Promise.resolve([]),
  ])
  users.value = results[0]; departments.value = results[1]; roles.value = results[2]
})
/** 显示真实角色名称，读取范围外的既有角色只显示其真实 ID。 */
function roleName(id: number): string { return roles.value.find((item) => item.id === id)?.name ?? '角色 #' + id }
/** 打开独立副本，不将密码或编辑过程写入用户列表。 */
function open(item?: User): void {
  editingId.value = item?.id
  Object.assign(form, { username: item?.username ?? '', display_name: item?.display_name ?? '', password: '', department_id: item?.department_id ?? null, role_ids: [...(item?.role_ids ?? [])], is_active: item?.is_active ?? true })
  dialog.value = true
  formRef.value?.clearValidate()
}
/** 创建时强制设置密码，编辑时空密码表示保持原密码。 */
async function save(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const body: UserWrite = { display_name: form.display_name.trim(), department_id: form.department_id, role_ids: [...form.role_ids], is_active: form.is_active }
  if (form.password) body.password = form.password
  await mutate(() => editingId.value === undefined ? api.createUser({ ...body, username: form.username.trim(), password: form.password }) : api.updateUser(editingId.value, body), () => { dialog.value = false; form.password = '' })
}
/** 停用立即影响已有会话，状态切换前明确告知操作者。 */
async function changeStatus(item: User): Promise<void> {
  const action = item.is_active ? '停用' : '启用'
  if (!(await confirm('确认' + action + '账号“' + item.username + '”？' + (item.is_active ? '该账号已有登录会话将失效。' : ''), action + '用户'))) return
  const body: UserWrite = { display_name: item.display_name, department_id: item.department_id, role_ids: [...item.role_ids], is_active: !item.is_active }
  await mutate(() => api.updateUser(item.id, body), undefined, '账号已' + action)
}
/** 删除账号前确认，保留最后管理员的限制由服务端执行。 */
async function remove(item: User): Promise<void> {
  if (await confirm('确认永久删除账号“' + item.username + '”？该账号将无法再登录。', '删除用户')) await mutate(() => api.deleteUser(item.id), undefined, '用户已删除')
}
onMounted(reload)
</script>
<template>
  <PageHeader title="用户管理" description="管理账号、直属部门与多角色分配，权限变更由服务端实时生效。"><el-button :loading="loading" :disabled="saving" @click="reload">刷新</el-button><el-button v-if="session.can('user:manage')" type="primary" :disabled="loading || saving" @click="open()">新增用户</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <section v-loading="loading" class="surface"><div class="surface-heading"><h3>用户列表 <span class="count-badge">{{ users.length }}</span></h3><el-input v-model="query" clearable placeholder="搜索用户名或显示名称" class="search-input" aria-label="搜索用户" /></div><el-table v-if="!error" :data="filtered" empty-text="没有匹配的用户"><el-table-column label="用户" min-width="180"><template #default="{ row }"><div class="cell-title">{{ row.display_name }} <el-tag v-if="row.id === session.user?.id" size="small" type="info">当前</el-tag></div><span class="cell-subtitle">{{ row.username }} · #{{ row.id }}</span></template></el-table-column><el-table-column label="直属部门" min-width="145"><template #default="{ row }">{{ row.department_id === null ? '未分配' : departments.find((item) => item.id === row.department_id)?.name ?? '部门 #' + row.department_id }}</template></el-table-column><el-table-column label="角色" min-width="200"><template #default="{ row }"><div v-if="row.role_ids.length" class="tag-list"><el-tag v-for="id in row.role_ids" :key="id" size="small" effect="plain">{{ roleName(id) }}</el-tag></div><span v-else class="muted">未分配</span></template></el-table-column><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column prop="permission_version" label="权限版本" width="100" /><el-table-column v-if="session.can('user:manage')" label="操作" width="200" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="saving" @click="open(row)">编辑</el-button><el-button link :type="row.is_active ? 'warning' : 'success'" :disabled="saving" @click="changeStatus(row)">{{ row.is_active ? '停用' : '启用' }}</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template></el-table-column></el-table></section>
  <el-dialog v-model="dialog" :title="editingId === undefined ? '新增用户' : '编辑用户'" width="600px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close @closed="form.password = ''"><el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving || !session.can('user:manage')" @submit.prevent="save"><div class="form-grid"><el-form-item label="用户名" prop="username"><el-input v-model="form.username" :disabled="editingId !== undefined" :maxlength="80" autocomplete="off" placeholder="请输入登录用户名" /></el-form-item><el-form-item label="显示名称" prop="display_name"><el-input v-model="form.display_name" :maxlength="100" placeholder="请输入显示名称" /></el-form-item></div><el-form-item :label="editingId === undefined ? '初始密码' : '重置密码'" prop="password"><el-input v-model="form.password" :maxlength="128" type="password" show-password autocomplete="new-password" :placeholder="editingId === undefined ? '请设置初始密码' : '留空保持原密码'" /></el-form-item><el-form-item label="直属部门"><DepartmentSelect v-model="form.department_id" :departments="departments" :disabled="!canReadDepartments" /><p v-if="!canReadDepartments" class="field-hint">当前没有部门列表读取权限，既有部门保持不变。需组织查看或部门管理权限才可选择。</p></el-form-item><el-form-item label="角色（可多选）"><el-select v-model="form.role_ids" multiple filterable clearable placeholder="请选择角色" :disabled="!canReadRoles" class="full-width"><el-option v-for="role in roles" :key="role.id" :label="role.name" :value="role.id" /><el-option v-for="id in form.role_ids.filter((id) => !roles.some((role) => role.id === id))" :key="id" :label="roleName(id)" :value="id" /></el-select><p v-if="!canReadRoles" class="field-hint">当前没有角色列表读取权限，既有角色保持不变。需组织查看或角色管理权限才可选择。</p></el-form-item><el-form-item label="账号状态"><el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" /></el-form-item></el-form><template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button v-if="session.can('user:manage')" type="primary" :loading="saving" @click="save">保存用户</el-button></template></el-dialog>
</template>
