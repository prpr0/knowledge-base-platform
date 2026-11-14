<!-- 管理真实知识条目、四维授权与获准正文读取。 -->
<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api, ApiError } from '../api'
import type { AclEntry, Department, KnowledgeDocument, KnowledgeManagedMetadata, KnowledgeMetadata, KnowledgePatch, Role, User } from '../types'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import AclEditor from '../components/AclEditor.vue'
import PageHeader from '../components/PageHeader.vue'
const session = useSessionStore()
const readable = ref<KnowledgeMetadata[]>([])
const managed = ref<KnowledgeManagedMetadata[]>([])
const departments = ref<Department[]>([])
const roles = ref<Role[]>([])
const users = ref<User[]>([])
const tab = ref(session.can('knowledge:read') ? 'read' : 'manage')
const search = ref('')
const editDialog = ref(false)
const editingId = ref<number>()
const formRef = ref<FormInstance>()
const form = reactive({ title: '', content: '', replaceContent: false, acl: [] as AclEntry[] })
const editorLoading = ref(false)
const editorError = ref('')
const aclDialog = ref(false)
const aclTarget = ref<KnowledgeManagedMetadata | null>(null)
const acl = ref<AclEntry[]>([])
const detailDrawer = ref(false)
const detail = ref<KnowledgeDocument | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
let detailRequest = 0
let editorRequest = 0
const canReadDepartments = computed(() => session.canAny(['organization:read', 'department:manage']))
const canReadRoles = computed(() => session.canAny(['organization:read', 'role:manage']))
const canReadUsers = computed(() => session.canAny(['organization:read', 'user:manage']))
const filtered = computed(() => (tab.value === 'read' ? readable.value : managed.value).filter((item) => item.title.toLowerCase().includes(search.value.trim().toLowerCase())))
const rules = computed<FormRules>(() => ({
  title: [{ required: true, whitespace: true, message: '请输入知识标题', trigger: 'blur' }],
  content: [{ required: editingId.value === undefined || form.replaceContent, whitespace: true, message: '请输入知识正文', trigger: 'blur' }],
}))
const { loading, saving, error, reload, mutate, confirm } = usePageState(async () => {
  readable.value = []; managed.value = []; departments.value = []; roles.value = []; users.value = []
  clearDetail()
  const manager = session.can('knowledge:manage')
  const results = await Promise.all([
    session.can('knowledge:read') ? api.knowledge() : Promise.resolve([]),
    manager ? api.managedKnowledge() : Promise.resolve([]),
    manager && canReadDepartments.value ? api.departments() : Promise.resolve([]),
    manager && canReadRoles.value ? api.roles() : Promise.resolve([]),
    manager && canReadUsers.value ? api.users() : Promise.resolve([]),
  ])
  readable.value = results[0]; managed.value = results[1]; departments.value = results[2]; roles.value = results[3]; users.value = results[4]
})
/** 权限刷新后切换到仍可访问的目录，并销毁已显示正文。 */
watch(() => session.user?.permission_version, () => { clearDetail(); if (editingId.value !== undefined) clearEditor() })
watch(() => session.user?.permissions, () => {
  if (!session.can('knowledge:read')) { clearDetail(); if (editingId.value !== undefined) clearEditor(); if (tab.value === 'read') tab.value = 'manage' }
  if (!session.can('knowledge:manage') && tab.value === 'manage') tab.value = 'read'
})
/** 关闭详情时清除正文并使尚未完成的请求失效。 */
function clearDetail(): void { detailRequest += 1; detail.value = null; detailDrawer.value = false; detailError.value = ''; detailLoading.value = false }
/** 读取每次都由正文 API 重新鉴权，管理身份不作放行依据。 */
async function read(item: KnowledgeMetadata): Promise<void> {
  const requestId = ++detailRequest
  detail.value = null; detailError.value = ''; detailLoading.value = true; detailDrawer.value = true
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：需要知识读取权限')
    const document = await api.readKnowledge(item.id)
    if (requestId === detailRequest) detail.value = document
  } catch (cause) { if (requestId === detailRequest) detailError.value = cause instanceof Error ? cause.message : '读取失败，请重试' }
  finally { if (requestId === detailRequest) detailLoading.value = false }
}
/** 编辑管理元数据时不隐式读取正文，可明确替换或另行申请读取。 */
function openEditor(item?: KnowledgeMetadata): void {
  editorRequest += 1
  editingId.value = item?.id
  Object.assign(form, { title: item?.title ?? '', content: '', replaceContent: item === undefined, acl: [] })
  editorError.value = ''; editorLoading.value = false; editDialog.value = true
  formRef.value?.clearValidate()
}
/** 有正文权限时显式载入编辑内容，拒绝时仍可编辑标题。 */
async function loadEditorContent(): Promise<void> {
  if (editingId.value === undefined) return
  const requestId = ++editorRequest
  editorLoading.value = true; editorError.value = ''
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：需要知识读取权限')
    const document = await api.readKnowledge(editingId.value)
    if (requestId === editorRequest) { form.content = document.content; form.replaceContent = true }
  } catch (cause) { if (requestId === editorRequest) editorError.value = cause instanceof Error ? cause.message : '正文读取失败' }
  finally { if (requestId === editorRequest) editorLoading.value = false }
}
/** 只有明确启用替换时才发送 content，避免用空值覆盖不可见正文。 */
async function save(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const id = editingId.value
  if (id === undefined) {
    await mutate(() => api.createKnowledge({ title: form.title.trim(), content: form.content, acl: [...form.acl] }), () => { editDialog.value = false })
  } else {
    const body: KnowledgePatch = { title: form.title.trim() }
    if (form.replaceContent) body.content = form.content
    await mutate(() => api.updateKnowledge(id, body), () => { editDialog.value = false })
  }
}
/** 单独管理四维授权，不利用管理响应获取正文。 */
function openAcl(item: KnowledgeManagedMetadata): void { aclTarget.value = item; acl.value = item.acl.map((entry) => ({ ...entry })); aclDialog.value = true }
/** ACL 更新仅提交明确选择的实体。 */
async function saveAcl(): Promise<void> {
  if (!aclTarget.value) return
  await mutate(() => api.setKnowledgeAcl(aclTarget.value!.id, [...acl.value]), () => { aclDialog.value = false }, '访问范围已更新')
}
/** 知识永久删除前确认，并在操作后清除已显示正文。 */
async function remove(item: KnowledgeMetadata): Promise<void> {
  if (await confirm('确认永久删除知识“' + item.title + '”？删除后所有用户将无法读取。', '删除知识')) await mutate(() => api.deleteKnowledge(item.id), undefined, '知识已删除')
}
/** 关闭编辑窗口时丢弃读取的正文和异步结果。 */
function clearEditor(): void { editorRequest += 1; form.content = ''; editorError.value = ''; editorLoading.value = false }
onMounted(reload)
</script>
<template>
  <PageHeader title="知识与授权" description="独立管理知识内容与访问范围，正文读取由当前权限和四维 ACL 共同决定。"><el-button :loading="loading" :disabled="saving" @click="reload">刷新</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :disabled="loading || saving" @click="openEditor()">新增知识</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <section v-loading="loading" class="surface"><div class="surface-heading"><el-tabs v-model="tab" class="directory-tabs"><el-tab-pane v-if="session.can('knowledge:read')" label="我可读取的知识" name="read" /><el-tab-pane v-if="session.can('knowledge:manage')" label="管理目录" name="manage" /></el-tabs><el-input v-model="search" clearable placeholder="搜索知识标题" class="search-input" aria-label="搜索知识" /></div><el-alert v-if="tab === 'manage'" title="管理目录仅展示管理元数据。读取正文仍需知识读取权限并命中 ACL。" type="info" :closable="false" class="page-alert" /><el-table v-if="!error" :data="filtered" :empty-text="tab === 'read' ? '暂无当前账号获准读取的知识' : '暂无知识，请创建首个条目'"><el-table-column prop="title" label="知识标题" min-width="240" /><el-table-column prop="id" label="ID" width="90" /><el-table-column prop="acl_version" label="授权版本" width="110" /><el-table-column v-if="tab === 'manage'" label="访问范围" min-width="150"><template #default="{ row }"><el-tag v-if="!row.acl.length" type="warning" size="small">默认拒绝</el-tag><el-tag v-else-if="row.acl.some((entry: AclEntry) => entry.type === 'global')" size="small">全局授权</el-tag><span v-else>{{ row.acl.length }} 项实体授权</span></template></el-table-column><el-table-column label="操作" :width="tab === 'manage' ? 295 : 110" fixed="right"><template #default="{ row }"><el-button v-if="session.can('knowledge:read')" link type="primary" :disabled="saving" @click="read(row)">{{ tab === 'manage' ? '读取正文' : '阅读' }}</el-button><template v-if="tab === 'manage' && session.can('knowledge:manage')"><el-button link type="primary" :disabled="saving" @click="openEditor(row)">编辑</el-button><el-button link type="primary" :disabled="saving" @click="openAcl(row)">访问范围</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template></template></el-table-column></el-table></section>
  <el-dialog v-model="editDialog" :title="editingId === undefined ? '新增知识' : '编辑知识'" width="760px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close @closed="clearEditor"><el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving || !session.can('knowledge:manage')" @submit.prevent="save"><el-form-item label="知识标题" prop="title"><el-input v-model="form.title" placeholder="请输入知识标题" /></el-form-item><div v-if="editingId !== undefined" class="editor-options"><el-checkbox v-model="form.replaceContent">替换正文</el-checkbox><el-button v-if="session.can('knowledge:read')" link type="primary" :loading="editorLoading" :disabled="saving" @click="loadEditorContent">读取当前正文后编辑</el-button><p class="field-hint">正文不会随管理元数据加载。未勾选替换时仅保存标题；勾选后请提供完整正文。</p></div><el-alert v-if="editorError" :title="editorError" type="error" :closable="false" show-icon class="page-alert" /><el-form-item v-if="editingId === undefined || form.replaceContent" label="知识正文" prop="content"><el-input v-model="form.content" type="textarea" :rows="10" placeholder="请输入完整知识正文" :disabled="editorLoading" /></el-form-item><template v-if="editingId === undefined"><h3>初始访问范围</h3><AclEditor v-model="form.acl" :departments="departments" :roles="roles" :users="users" :can-read-departments="canReadDepartments" :can-read-roles="canReadRoles" :can-read-users="canReadUsers" :disabled="saving" /></template></el-form><template #footer><el-button :disabled="saving" @click="editDialog = false">取消</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :loading="saving" :disabled="editorLoading" @click="save">保存知识</el-button></template></el-dialog>
  <el-dialog v-model="aclDialog" title="设置知识访问范围" width="650px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close><p class="dialog-description">{{ aclTarget?.title }} · 授权版本 {{ aclTarget?.acl_version }}</p><el-form label-position="top" :disabled="saving || !session.can('knowledge:manage')"><AclEditor v-model="acl" :departments="departments" :roles="roles" :users="users" :can-read-departments="canReadDepartments" :can-read-roles="canReadRoles" :can-read-users="canReadUsers" :disabled="saving || !session.can('knowledge:manage')" /></el-form><template #footer><el-button :disabled="saving" @click="aclDialog = false">取消</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :loading="saving" @click="saveAcl">保存访问范围</el-button></template></el-dialog>
  <el-drawer v-model="detailDrawer" title="知识正文" size="min(760px, 100vw)" @closed="clearDetail"><div v-loading="detailLoading" class="document-reader"><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon /><article v-else-if="detail"><div class="eyebrow">KNOWLEDGE #{{ detail.id }}</div><h2>{{ detail.title }}</h2><div class="document-meta">授权版本 {{ detail.acl_version }} · 已通过当前身份与 ACL 校验</div><div class="document-content">{{ detail.content }}</div></article></div></el-drawer>
</template>
