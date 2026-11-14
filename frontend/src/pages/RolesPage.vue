<!-- 维护角色及精确菜单按钮权限集合。 -->
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '../api'
import type { Permission, Role } from '../types'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import PageHeader from '../components/PageHeader.vue'
import PermissionTree from '../components/PermissionTree.vue'
const session = useSessionStore()
const roles = ref<Role[]>([])
const permissions = ref<Permission[]>([])
const dialog = ref(false)
const editingId = ref<number>()
const formRef = ref<FormInstance>()
const form = reactive({ name: '', permissions: [] as string[] })
const rules: FormRules = { name: [{ required: true, whitespace: true, message: '请输入角色名称', trigger: 'blur' }] }
const { loading, saving, error, reload, mutate, confirm } = usePageState(async () => {
  roles.value = []; permissions.value = []
  if (!session.canAny(['organization:read', 'role:manage'])) return
  const results = await Promise.all([api.roles(), api.permissions()])
  roles.value = results[0]; permissions.value = results[1]
})
/** 查看或编辑角色的精确权限集合，不生成管理通配符。 */
function open(item?: Role): void {
  editingId.value = item?.id
  form.name = item?.name ?? ''
  form.permissions = [...(item?.permissions ?? [])]
  dialog.value = true
  formRef.value?.clearValidate()
}
/** 空权限角色允许保存，但不具备任何菜单或操作能力。 */
async function save(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const body = { name: form.name.trim(), permissions: [...form.permissions] }
  await mutate(() => editingId.value === undefined ? api.createRole(body) : api.updateRole(editingId.value, body), () => { dialog.value = false })
}
/** 删除角色可能影响当前账号，操作后重新读取当前身份。 */
async function remove(item: Role): Promise<void> {
  if (await confirm('确认删除角色“' + item.name + '”？分配该角色的用户将失去相应授权。', '删除角色')) await mutate(() => api.deleteRole(item.id), undefined, '角色已删除')
}
onMounted(reload)
</script>
<template>
  <PageHeader title="角色权限" description="精确分配菜单与操作权限；知识管理权限不隐含知识正文读取权限。"><el-button :loading="loading" :disabled="saving" @click="reload">刷新</el-button><el-button v-if="session.can('role:manage')" type="primary" :disabled="loading || saving" @click="open()">新增角色</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <section v-loading="loading" class="surface"><div class="surface-heading"><h3>角色列表 <span class="count-badge">{{ roles.length }}</span></h3><span class="muted">权限由服务端清单提供</span></div><el-table v-if="!error" :data="roles" empty-text="暂无角色"><el-table-column prop="name" label="角色名称" min-width="170" /><el-table-column prop="id" label="ID" width="80" /><el-table-column label="权限" min-width="340"><template #default="{ row }"><div v-if="row.permissions.length" class="tag-list"><el-tag v-for="code in row.permissions" :key="code" size="small" effect="plain">{{ permissions.find((item) => item.code === code)?.label ?? code }}</el-tag></div><span v-else class="muted">无权限</span></template></el-table-column><el-table-column label="操作" width="160" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="saving" @click="open(row)">{{ session.can('role:manage') ? '编辑' : '查看权限' }}</el-button><el-button v-if="session.can('role:manage')" link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template></el-table-column></el-table></section>
  <el-dialog v-model="dialog" :title="session.can('role:manage') ? editingId === undefined ? '新增角色' : '编辑角色' : '查看角色权限'" width="650px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close><el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving || !session.can('role:manage')" @submit.prevent="save"><el-form-item label="角色名称" prop="name"><el-input v-model="form.name" placeholder="请输入角色名称" /></el-form-item><el-form-item label="菜单与操作权限"><div class="full-width"><PermissionTree v-model="form.permissions" :permissions="permissions" :disabled="saving || !session.can('role:manage')" /></div></el-form-item></el-form><template #footer><el-button :disabled="saving" @click="dialog = false">{{ session.can('role:manage') ? '取消' : '关闭' }}</el-button><el-button v-if="session.can('role:manage')" type="primary" :loading="saving" @click="save">保存角色</el-button></template></el-dialog>
</template>
