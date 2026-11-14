<!-- 维护部门树与直属父关系。 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '../api'
import type { Department } from '../types'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import DepartmentSelect from '../components/DepartmentSelect.vue'
import PageHeader from '../components/PageHeader.vue'
const session = useSessionStore()
const departments = ref<Department[]>([])
const dialog = ref(false)
const editingId = ref<number>()
const formRef = ref<FormInstance>()
const form = reactive({ name: '', parent_id: null as number | null })
const rules: FormRules = { name: [{ required: true, whitespace: true, message: '请输入部门名称', trigger: 'blur' }] }
const { loading, saving, error, reload, mutate, confirm } = usePageState(async () => {
  departments.value = []
  if (session.canAny(['organization:read', 'department:manage'])) departments.value = await api.departments()
})
interface DepartmentNode extends Department { children: DepartmentNode[] }
/** 用父部门外键生成展示树，不给上级部门继承知识读取权。 */
const tree = computed(() => {
  const map = new Map<number, DepartmentNode>(departments.value.map((item) => [item.id, { ...item, children: [] }]))
  const roots: DepartmentNode[] = []
  for (const item of departments.value) {
    const node = map.get(item.id)!
    const parent = item.parent_id === null ? undefined : map.get(item.parent_id)
    if (parent && parent !== node) parent.children.push(node)
    else roots.push(node)
  }
  return roots
})
/** 打开新增或编辑表单，提交前不改变列表原值。 */
function open(item?: Department): void {
  editingId.value = item?.id
  form.name = item?.name ?? ''
  form.parent_id = item?.parent_id ?? null
  dialog.value = true
  formRef.value?.clearValidate()
}
/** 校验名称并保存真实部门。 */
async function save(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const body = { name: form.name.trim(), parent_id: form.parent_id }
  await mutate(() => editingId.value === undefined ? api.createDepartment(body) : api.updateDepartment(editingId.value, body), () => { dialog.value = false })
}
/** 部门删除限制由服务端检查，失败时展示具体原因。 */
async function remove(item: Department): Promise<void> {
  if (await confirm('确认删除部门“' + item.name + '”？有子部门或所属用户时无法删除。', '删除部门')) await mutate(() => api.deleteDepartment(item.id), undefined, '部门已删除')
}
onMounted(reload)
</script>
<template>
  <PageHeader title="部门管理" description="维护部门树与直属关系。知识授权仅匹配用户直属部门。"><el-button :loading="loading" :disabled="saving" @click="reload">刷新</el-button><el-button v-if="session.can('department:manage')" type="primary" :disabled="loading || saving" @click="open()">新增部门</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <section v-loading="loading" class="surface"><div class="surface-heading"><h3>组织层级</h3><span class="muted">{{ departments.length }} 个部门</span></div><el-table v-if="!error" :data="tree" row-key="id" default-expand-all :tree-props="{ children: 'children' }" empty-text="暂无部门，请创建首个部门"><el-table-column prop="name" label="部门名称" min-width="220" /><el-table-column prop="id" label="部门 ID" width="110" /><el-table-column label="上级部门" min-width="160"><template #default="{ row }">{{ departments.find((item) => item.id === row.parent_id)?.name ?? '根部门' }}</template></el-table-column><el-table-column v-if="session.can('department:manage')" label="操作" width="150" fixed="right"><template #default="{ row }"><el-button link type="primary" :disabled="saving" @click="open(row)">编辑</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template></el-table-column></el-table></section>
  <el-dialog v-model="dialog" :title="editingId === undefined ? '新增部门' : '编辑部门'" width="520px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close><el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving" @submit.prevent="save"><el-form-item label="部门名称" prop="name"><el-input v-model="form.name" placeholder="请输入部门名称" /></el-form-item><el-form-item label="上级部门"><DepartmentSelect v-model="form.parent_id" :departments="departments" :exclude-id="editingId" /><p class="field-hint">不选上级时创建根部门，不能将部门移动到自身或其子部门下。</p></el-form-item></el-form><template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button v-if="session.can('department:manage')" type="primary" :loading="saving" @click="save">保存部门</el-button></template></el-dialog>
</template>
