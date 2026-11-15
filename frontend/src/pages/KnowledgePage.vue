<!-- 管理知识台账、版本、四维授权及重新鉴权的正文、切片和原文件。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { api, ApiError } from '../api'
import type { AclEntry, KnowledgeChunk, KnowledgeDocument, KnowledgeManagedMetadata, KnowledgeMetadata, KnowledgePatch, KnowledgeVersion } from '../types'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import { useAclCandidates } from '../composables/useAclCandidates'
import { taskStatusLabel } from '../importStatus'
import AclEditor from '../components/AclEditor.vue'
import PageHeader from '../components/PageHeader.vue'

const session = useSessionStore()
const router = useRouter()
const readable = ref<KnowledgeMetadata[]>([])
const managed = ref<KnowledgeManagedMetadata[]>([])
const { departments, roles, users, canReadDepartments, canReadRoles, canReadUsers, loadCandidates } = useAclCandidates()
const tab = ref(session.can('knowledge:read') ? 'read' : 'manage')
const search = ref('')
const editDialog = ref(false)
const editingId = ref<number>()
const originalTitle = ref('')
const formRef = ref<FormInstance>()
const form = reactive({ title: '', category: '', content: '', replaceContent: false, acl: [] as AclEntry[] })
const editorLoading = ref(false)
const editorError = ref('')
const aclDialog = ref(false)
const aclTarget = ref<KnowledgeManagedMetadata | null>(null)
const acl = ref<AclEntry[]>([])
const detailDrawer = ref(false)
const detail = ref<KnowledgeDocument | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const versionsDialog = ref(false)
const versions = ref<KnowledgeVersion[]>([])
const versionTarget = ref<KnowledgeMetadata | null>(null)
const versionsLoading = ref(false)
const versionsError = ref('')
const chunksDrawer = ref(false)
const chunks = ref<KnowledgeChunk[]>([])
const chunkTarget = ref<KnowledgeMetadata | null>(null)
const chunksLoading = ref(false)
const chunksError = ref('')
const chunkDialog = ref(false)
const editingChunk = ref<KnowledgeChunk | null>(null)
const chunkContent = ref('')
const chunkSaving = ref(false)
const chunkError = ref('')
const downloadingId = ref<number>()
const rebuildTaskId = ref<string>()
let detailRequest = 0
let editorRequest = 0
let chunksRequest = 0
let versionsRequest = 0
let downloadRequest = 0
let disposed = false

const filtered = computed(() => (tab.value === 'read' ? readable.value : managed.value).filter((item) => item.title.toLowerCase().includes(search.value.trim().toLowerCase())))
const rules = computed<FormRules>(() => ({
  title: [{ required: true, whitespace: true, message: '请输入知识标题', trigger: 'blur' }],
  content: [{ required: editingId.value === undefined || form.replaceContent, whitespace: true, message: '请输入知识正文', trigger: 'blur' }],
}))
const { loading, saving, error, reload, mutate, confirm } = usePageState(async () => {
  readable.value = []; managed.value = []
  clearProtectedContent()
  const result = await Promise.all([
    session.can('knowledge:read') ? api.knowledge() : Promise.resolve([]),
    session.can('knowledge:manage') ? api.managedKnowledge() : Promise.resolve([]),
    loadCandidates(),
  ])
  if (disposed || !session.authenticated) return
  readable.value = result[0]; managed.value = result[1]
})

/** 关闭正文时使尚未完成的请求失效。 */
function clearDetail(): void { detailRequest += 1; detail.value = null; detailDrawer.value = false; detailError.value = ''; detailLoading.value = false }

/** 清除获准切片及编辑副本，撤权后不保留旧正文。 */
function clearChunks(): void {
  chunksRequest += 1; chunks.value = []; chunkTarget.value = null; chunksDrawer.value = false; chunksLoading.value = false; chunksError.value = ''
  chunkDialog.value = false; editingChunk.value = null; chunkContent.value = ''; chunkError.value = ''
}

/** 关闭编辑窗口时丢弃读取的正文和异步结果。 */
function clearEditor(): void { editorRequest += 1; form.content = ''; editorError.value = ''; editorLoading.value = false }

/** 更新、删除和权限变更立即清除页面中所有正文副本及待下载响应。 */
function clearProtectedContent(): void { clearDetail(); clearChunks(); clearEditor(); downloadRequest += 1; downloadingId.value = undefined }

/** 每次正文读取都先刷新身份，再由正文 API 独立校验 ACL。 */
async function read(item: KnowledgeMetadata): Promise<void> {
  const requestId = ++detailRequest
  detail.value = null; detailError.value = ''; detailLoading.value = true; detailDrawer.value = true
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：需要知识读取权限，管理权限不授予正文读取权')
    if (requestId !== detailRequest || disposed) return
    const document = await api.readKnowledge(item.id)
    if (requestId === detailRequest && !disposed) detail.value = document
  } catch (cause) { if (requestId === detailRequest && !disposed) detailError.value = cause instanceof Error ? cause.message : '读取失败，请重试' }
  finally { if (requestId === detailRequest && !disposed) detailLoading.value = false }
}

/** 管理元数据不隐式读取正文，旧文本单位没有活动版本仍可明确替换正文。 */
function openEditor(item?: KnowledgeMetadata): void {
  editorRequest += 1; editingId.value = item?.id; originalTitle.value = item?.title ?? ''
  Object.assign(form, { title: item?.title ?? '', category: item?.category ?? '', content: '', replaceContent: item === undefined, acl: [] })
  editorError.value = ''; editorLoading.value = false; editDialog.value = true
  formRef.value?.clearValidate()
}

/** 读取被明确授权的正文后编辑，403 明示且不回填任何管理侧正文。 */
async function loadEditorContent(): Promise<void> {
  if (editingId.value === undefined) return
  const id = editingId.value
  const requestId = ++editorRequest
  editorLoading.value = true; editorError.value = ''; form.content = ''
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：需要知识读取权限')
    if (requestId !== editorRequest || disposed) return
    const document = await api.readKnowledge(id)
    if (requestId === editorRequest && !disposed) { form.content = document.content; form.replaceContent = true }
  } catch (cause) { if (requestId === editorRequest && !disposed) editorError.value = cause instanceof Error ? cause.message : '正文读取失败' }
  finally { if (requestId === editorRequest && !disposed) editorLoading.value = false }
}

/** 展示真实重建任务号，提醒候选版本完成前继续使用旧活动版本。 */
function showRebuild(taskId?: string | null): void {
  if (taskId !== undefined && taskId !== null) {
    rebuildTaskId.value = taskId
    ElMessage.info('后台重建任务 #' + taskId + ' 已创建，请在导入中心查看进度')
  }
}

/** 仅明确替换时发送完整正文，标题变化和正文编辑按返回任务后台重建。 */
async function save(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const id = editingId.value
  let result: KnowledgeManagedMetadata | undefined
  if (id === undefined) {
    await mutate(async () => { result = await api.createKnowledge({ title: form.title.trim(), content: form.content, acl: [...form.acl] }) }, () => { clearProtectedContent(); editDialog.value = false; showRebuild(result?.rebuild_task_id) })
  } else {
    const body: KnowledgePatch = { category: form.category.trim() || '未分类' }
    if (form.title.trim() !== originalTitle.value) body.title = form.title.trim()
    if (form.replaceContent) body.content = form.content
    await mutate(async () => { result = await api.updateKnowledge(id, body) }, () => { clearProtectedContent(); editDialog.value = false; showRebuild(result?.rebuild_task_id) })
  }
}

/** 四维授权独立更新，不借管理响应读取正文。 */
function openAcl(item: KnowledgeManagedMetadata): void { aclTarget.value = item; acl.value = item.acl.map((entry) => ({ ...entry })); aclDialog.value = true }

/** ACL 更新后立即销毁所有可能失去授权的正文副本。 */
async function saveAcl(): Promise<void> {
  if (!aclTarget.value) return
  await mutate(() => api.setKnowledgeAcl(aclTarget.value!.id, [...acl.value]), () => { clearProtectedContent(); aclDialog.value = false }, '访问范围已更新')
}

/** 启停由服务端即时生效，停用前说明正文与检索同时停止。 */
async function toggleActive(item: KnowledgeMetadata): Promise<void> {
  if (!await confirm((item.is_active ? '停用' : '启用') + '知识“' + item.title + '”？停用时正文、切片、下载和检索均不可用。', '变更知识状态')) return
  await mutate(() => api.updateKnowledge(item.id, { is_active: !item.is_active }), clearProtectedContent, item.is_active ? '知识已停用' : '知识已启用')
}

/** 永久删除前确认，成功后销毁已显示正文与切片。 */
async function remove(item: KnowledgeMetadata): Promise<void> {
  if (await confirm('确认永久删除知识“' + item.title + '”？删除后所有用户将无法读取。', '删除知识')) await mutate(() => api.deleteKnowledge(item.id), clearProtectedContent, '知识已删除')
}

/** 版本列表只读取管理元数据，失败版本保留实际原因。 */
async function showVersions(item: KnowledgeMetadata): Promise<void> {
  const requestId = ++versionsRequest
  versionTarget.value = item; versions.value = []; versionsError.value = ''; versionsLoading.value = true; versionsDialog.value = true
  try {
    const value = await api.knowledgeVersions(item.id)
    if (requestId === versionsRequest && !disposed) versions.value = value
  } catch (cause) { if (requestId === versionsRequest && !disposed) versionsError.value = cause instanceof Error ? cause.message : '版本列表读取失败' }
  finally { if (requestId === versionsRequest && !disposed) versionsLoading.value = false }
}

/** 版本弹窗关闭后丢弃迟到响应。 */
function clearVersions(): void { versionsRequest += 1; versions.value = []; versionTarget.value = null; versionsError.value = ''; versionsLoading.value = false }

/** 只读取当前活动版本获准切片，每次请求均由后端重新执行 ACL。 */
async function showChunks(item: KnowledgeMetadata): Promise<void> {
  clearChunks()
  const requestId = ++chunksRequest
  chunkTarget.value = item; chunksDrawer.value = true; chunksLoading.value = true
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：管理权限不授予切片正文读取权')
    if (requestId !== chunksRequest || disposed) return
    const value = await api.knowledgeChunks(item.id)
    if (requestId === chunksRequest && !disposed) chunks.value = value
  } catch (cause) { if (requestId === chunksRequest && !disposed) chunksError.value = cause instanceof Error ? cause.message : '切片读取失败' }
  finally { if (requestId === chunksRequest && !disposed) chunksLoading.value = false }
}

/** 编辑入口只使用已经获准读取的切片正文。 */
function openChunkEditor(chunk: KnowledgeChunk): void { editingChunk.value = chunk; chunkContent.value = chunk.content; chunkError.value = ''; chunkDialog.value = true }

/** 切片编辑创建完整候选版本，随后跳转真实任务页查看原子发布进度。 */
async function saveChunk(): Promise<void> {
  if (!editingChunk.value || chunkSaving.value) return
  if (!chunkContent.value.trim()) { chunkError.value = '请输入完整切片正文'; return }
  chunkSaving.value = true; chunkError.value = ''
  try {
    const task = await api.updateChunk(editingChunk.value.id, chunkContent.value)
    if (disposed) return
    clearProtectedContent()
    ElMessage.info('后台重建任务 #' + task.id + ' 已创建')
    await router.push({ path: '/imports', query: { task: task.id } })
  } catch (cause) { if (!disposed) chunkError.value = cause instanceof Error ? cause.message : '切片保存失败' }
  finally { if (!disposed) chunkSaving.value = false }
}

/** 通过带 Bearer 的 fetch 取得 Blob 后临时下载，不暴露静态文件地址。 */
async function download(item: KnowledgeMetadata): Promise<void> {
  if (downloadingId.value !== undefined) return
  const requestId = ++downloadRequest
  downloadingId.value = item.id
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：管理权限不授予原文件读取权')
    if (requestId !== downloadRequest || disposed) return
    const value = await api.downloadKnowledge(item.id)
    if (requestId !== downloadRequest || disposed) return
    const url = URL.createObjectURL(value.blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = (value.filename ?? item.title + (item.format ? '.' + item.format : '')).replace(/[\\/]/g, '_')
    document.body.appendChild(anchor); anchor.click(); anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (cause) {
    if (requestId === downloadRequest && !disposed) ElMessage.error(cause instanceof ApiError && cause.status === 404 ? '暂无可下载的原文件；旧文本知识或未发布的版本可能没有原文件' : cause instanceof Error ? cause.message : '原文件下载失败')
  } finally { if (requestId === downloadRequest && !disposed) downloadingId.value = undefined }
}

/** 显示服务端时间，缺省字段明确以破折号表示。 */
function displayTime(value: string | null): string { return value ? new Date(value).toLocaleString('zh-CN') : '—' }

watch(() => session.user?.permission_version, () => { clearProtectedContent(); clearVersions() })
watch(() => session.user?.permissions, () => {
  if (!session.can('knowledge:read')) { clearProtectedContent(); if (tab.value === 'read') tab.value = 'manage' }
  if (!session.can('knowledge:manage')) { editDialog.value = false; aclDialog.value = false; versionsDialog.value = false; clearVersions(); if (tab.value === 'manage') tab.value = 'read' }
})
onMounted(reload)
onBeforeUnmount(() => { disposed = true; clearProtectedContent(); clearVersions() })
</script>

<template>
  <PageHeader title="知识与授权" description="管理知识生命周期与访问范围，正文、切片和原文件均需当前知识读取权限及四维 ACL。">
    <el-button :loading="loading" :disabled="saving" @click="reload">刷新</el-button>
    <template v-if="session.can('knowledge:manage')"><el-button @click="router.push('/imports')">导入文档</el-button><el-button type="primary" :disabled="loading || saving" @click="openEditor()">新增文本知识</el-button></template>
  </PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <el-alert v-if="rebuildTaskId !== undefined" :title="'后台重建任务 #' + rebuildTaskId + ' 已创建，成功后切换活动版本；请在任务页查看进度。'" type="info" :closable="false" class="page-alert"><el-button link type="primary" @click="router.push({ path: '/imports', query: { task: rebuildTaskId } })">查看任务 #{{ rebuildTaskId }}</el-button></el-alert>
  <section v-loading="loading" class="surface">
    <div class="surface-heading"><el-tabs v-model="tab" class="directory-tabs"><el-tab-pane v-if="session.can('knowledge:read')" label="我可读取的知识" name="read" /><el-tab-pane v-if="session.can('knowledge:manage')" label="管理目录" name="manage" /></el-tabs><el-input v-model="search" clearable placeholder="搜索知识标题" class="search-input" aria-label="搜索知识" /></div>
    <el-alert v-if="tab === 'manage'" title="管理目录只授予元数据维护权，正文、切片和原文件仍需 knowledge:read 并命中 ACL；403 表示当前账号未获读取授权。" type="info" :closable="false" class="page-alert" />
    <el-table v-if="!error" :data="filtered" :empty-text="tab === 'read' ? '暂无当前账号获准读取的知识' : '暂无知识，请创建或导入文档'">
      <el-table-column prop="title" label="知识标题" min-width="210" />
      <el-table-column prop="id" label="ID" width="75" />
      <el-table-column label="格式 / 分类" min-width="130"><template #default="{ row }">{{ row.format?.toUpperCase() || '文本' }}<div class="field-hint">{{ row.category || '未分类' }}</div></template></el-table-column>
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="活动版本" min-width="115"><template #default="{ row }">{{ row.active_version_id ? '#' + row.active_version_id : '无活动版本' }}</template></el-table-column>
      <el-table-column label="更新时间" min-width="175"><template #default="{ row }">{{ displayTime(row.updated_at) }}</template></el-table-column>
      <el-table-column prop="acl_version" label="授权版本" width="100" />
      <el-table-column v-if="tab === 'manage'" label="访问范围" min-width="140"><template #default="{ row }"><el-tag v-if="!row.acl.length" type="warning" size="small">默认拒绝</el-tag><el-tag v-else-if="row.acl.some((entry: AclEntry) => entry.type === 'global')" size="small">全局授权</el-tag><span v-else>{{ row.acl.length }} 项实体授权</span></template></el-table-column>
      <el-table-column label="操作" min-width="300" fixed="right"><template #default="{ row }"><div class="table-actions">
        <template v-if="session.can('knowledge:read')"><el-button link type="primary" :disabled="saving" @click="read(row)">正文</el-button><el-button link type="primary" :disabled="saving" @click="showChunks(row)">切片</el-button><el-button link type="primary" :loading="downloadingId === row.id" :disabled="saving || (downloadingId !== undefined && downloadingId !== row.id)" @click="download(row)">原文件</el-button></template>
        <template v-if="tab === 'manage' && session.can('knowledge:manage')"><el-button link type="primary" :disabled="saving" @click="openEditor(row)">编辑</el-button><el-button link type="primary" :disabled="saving" @click="openAcl(row)">访问范围</el-button><el-button link type="primary" :disabled="saving" @click="showVersions(row)">版本</el-button><el-button link type="warning" :disabled="saving" @click="toggleActive(row)">{{ row.is_active ? '停用' : '启用' }}</el-button><el-button link type="danger" :disabled="saving" @click="remove(row)">删除</el-button></template>
      </div></template></el-table-column>
    </el-table>
  </section>
  <el-dialog v-model="editDialog" :title="editingId === undefined ? '新增文本知识' : '编辑知识'" width="760px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close @closed="clearEditor">
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving || !session.can('knowledge:manage')" @submit.prevent="save">
      <el-form-item label="知识标题" prop="title"><el-input v-model="form.title" placeholder="请输入知识标题" /></el-form-item>
      <el-form-item v-if="editingId !== undefined" label="分类"><el-input v-model="form.category" placeholder="留空表示未分类" maxlength="100" /></el-form-item>
      <div v-if="editingId !== undefined" class="editor-options"><el-checkbox v-model="form.replaceContent">替换正文</el-checkbox><el-button v-if="session.can('knowledge:read')" link type="primary" :loading="editorLoading" :disabled="saving" @click="loadEditorContent">读取当前正文后编辑</el-button><p class="field-hint">管理元数据不加载正文。勾选替换后需提供完整正文；标题或正文编辑可能创建后台重建任务，成功后切换活动版本。</p></div>
      <el-alert v-if="editorError" :title="editorError" type="error" :closable="false" show-icon class="page-alert" />
      <el-form-item v-if="editingId === undefined || form.replaceContent" label="知识正文" prop="content"><el-input v-model="form.content" type="textarea" :rows="10" placeholder="请输入完整知识正文" :disabled="editorLoading" /></el-form-item>
      <template v-if="editingId === undefined"><h3>初始访问范围</h3><AclEditor v-model="form.acl" :departments="departments" :roles="roles" :users="users" :can-read-departments="canReadDepartments" :can-read-roles="canReadRoles" :can-read-users="canReadUsers" :disabled="saving" /></template>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="editDialog = false">取消</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :loading="saving" :disabled="editorLoading" @click="save">保存知识</el-button></template>
  </el-dialog>
  <el-dialog v-model="aclDialog" title="设置知识访问范围" width="650px" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" destroy-on-close>
    <p class="dialog-description">{{ aclTarget?.title }} · 授权版本 {{ aclTarget?.acl_version }}</p>
    <el-form label-position="top" :disabled="saving || !session.can('knowledge:manage')"><AclEditor v-model="acl" :departments="departments" :roles="roles" :users="users" :can-read-departments="canReadDepartments" :can-read-roles="canReadRoles" :can-read-users="canReadUsers" :disabled="saving || !session.can('knowledge:manage')" /></el-form>
    <template #footer><el-button :disabled="saving" @click="aclDialog = false">取消</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :loading="saving" @click="saveAcl">保存访问范围</el-button></template>
  </el-dialog>
  <el-drawer v-model="detailDrawer" title="知识正文" size="min(760px, 100vw)" @closed="clearDetail">
    <div v-loading="detailLoading" class="document-reader"><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon /><article v-else-if="detail"><div class="eyebrow">KNOWLEDGE #{{ detail.id }}</div><h2>{{ detail.title }}</h2><div class="document-meta">{{ detail.active_version_id ? '活动版本 #' + detail.active_version_id : '旧文本单位 · 无活动版本' }} · 授权版本 {{ detail.acl_version }} · 已通过当前身份与 ACL 校验</div><div class="document-content">{{ detail.content }}</div></article></div>
  </el-drawer>
  <el-dialog v-model="versionsDialog" :title="'版本记录 · ' + (versionTarget?.title ?? '')" width="1000px" @closed="clearVersions">
    <el-alert v-if="versionsError" :title="versionsError" type="error" :closable="false" show-icon class="page-alert" />
    <el-table v-loading="versionsLoading" :data="versions" empty-text="暂无版本记录，旧文本单位可能尚未建立索引">
      <el-table-column label="版本" width="120"><template #default="{ row }">v{{ row.number }} / #{{ row.id }}<el-tag v-if="row.id === versionTarget?.active_version_id" size="small">活动</el-tag></template></el-table-column>
      <el-table-column prop="title" label="标题" min-width="160" />
      <el-table-column label="状态" min-width="100"><template #default="{ row }">{{ taskStatusLabel(row.status) }}</template></el-table-column>
      <el-table-column prop="file_format" label="格式" width="85" />
      <el-table-column label="创建时间" min-width="175"><template #default="{ row }">{{ displayTime(row.created_at) }}</template></el-table-column>
      <el-table-column prop="sha256" label="SHA-256" min-width="150" show-overflow-tooltip />
      <el-table-column label="错误原因" min-width="220"><template #default="{ row }"><span class="error-text">{{ row.error || '—' }}</span></template></el-table-column>
    </el-table>
  </el-dialog>
  <el-drawer v-model="chunksDrawer" :title="'活动版本切片 · ' + (chunkTarget?.title ?? '')" size="min(880px, 100vw)" @closed="clearChunks">
    <div v-loading="chunksLoading" class="document-reader">
      <el-alert v-if="chunksError" :title="chunksError" type="error" :closable="false" show-icon />
      <el-empty v-else-if="!chunksLoading && !chunks.length" :description="chunkTarget?.active_version_id ? '当前活动版本没有可展示切片' : '暂无活动版本；旧文本知识需完成索引重建后才能浏览切片'" />
      <article v-for="chunk in chunks" :key="chunk.id" class="chunk-card">
        <div class="surface-heading"><h3>片段 {{ chunk.sequence }} · #{{ chunk.id }}</h3><el-button v-if="session.can('knowledge:manage')" link type="primary" :disabled="chunkSaving" @click="openChunkEditor(chunk)">编辑切片</el-button></div>
        <div class="document-meta">版本 #{{ chunk.version_id }} · {{ chunk.token_count }} token<span v-if="chunk.section"> · {{ chunk.section }}</span><span v-if="chunk.page !== null"> · 第 {{ chunk.page }} 页</span><span v-if="chunk.location"> · {{ chunk.location }}</span></div>
        <div class="document-content">{{ chunk.content }}</div>
      </article>
    </div>
  </el-drawer>
  <el-dialog v-model="chunkDialog" title="编辑切片并重建版本" width="760px" :close-on-click-modal="false" :close-on-press-escape="!chunkSaving" :show-close="!chunkSaving" @closed="chunkContent = ''; editingChunk = null">
    <el-alert title="保存后在后台重建完整候选版本，成功后原子切换；页面将打开任务进度。" type="info" :closable="false" class="page-alert" />
    <el-alert v-if="chunkError" :title="chunkError" type="error" :closable="false" class="page-alert" />
    <el-input v-model="chunkContent" type="textarea" :rows="14" :disabled="chunkSaving || !session.can('knowledge:manage')" aria-label="切片完整正文" />
    <template #footer><el-button :disabled="chunkSaving" @click="chunkDialog = false">取消</el-button><el-button v-if="session.can('knowledge:manage')" type="primary" :loading="chunkSaving" @click="saveChunk">保存并查看任务</el-button></template>
  </el-dialog>
</template>
