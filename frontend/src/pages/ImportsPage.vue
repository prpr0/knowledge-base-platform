<!-- 真实文档导入、目录递归收集及数据库任务进度控制台。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import type { AclEntry, ImportTask } from '../types'
import { useSessionStore } from '../stores/session'
import { useAclCandidates } from '../composables/useAclCandidates'
import { taskIdFromQuery, taskIsPending, taskStageLabel, taskStatusLabel } from '../importStatus'
import AclEditor from '../components/AclEditor.vue'
import PageHeader from '../components/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const { departments, roles, users, canReadDepartments, canReadRoles, canReadUsers, loadCandidates } = useAclCandidates()
const files = ref<{ file: File; path: string }[]>([])
const acl = ref<AclEntry[]>([])
const category = ref('')
const tasks = ref<ImportTask[]>([])
const selected = ref<ImportTask | null>(null)
const loading = ref(false)
const submitting = ref(false)
const retrying = ref(false)
const collecting = ref(false)
const dragging = ref(false)
const error = ref('')
const selectionError = ref('')
const candidateError = ref('')
const singleInput = ref<HTMLInputElement>()
const batchInput = ref<HTMLInputElement>()
const folderInput = ref<HTMLInputElement>()
const selectedId = computed(() => taskIdFromQuery(route.query.task))
let timer: ReturnType<typeof setTimeout> | undefined
let revision = 0
let disposed = false
let navigateOnSuccess = selectedId.value !== undefined

/** 页面销毁或手动刷新时取消下一次轮询。 */
function stopPolling(): void { if (timer !== undefined) { clearTimeout(timer); timer = undefined } }

/** 仅依据数据库当前状态安排下次刷新，失败时留在页面提供显式刷新。 */
function scheduleRefresh(): void {
  stopPolling()
  if (!disposed && session.authenticated && (tasks.value.some((task) => taskIsPending(task.status)) || (selected.value && taskIsPending(selected.value.status)))) {
    timer = setTimeout(() => { void refreshTasks() }, 2500)
  }
}

/** 同步列表与选中任务，旧页面或旧选择的迟到响应不得覆盖新状态。 */
async function refreshTasks(): Promise<void> {
  stopPolling()
  const current = ++revision
  const id = selectedId.value
  loading.value = true; error.value = ''
  try {
    await session.refresh()
    if (!session.can('knowledge:manage')) return
    const [list, detail] = await Promise.all([api.imports(), id === undefined ? Promise.resolve(null) : api.importTask(id)])
    if (disposed || current !== revision) return
    tasks.value = list
    selected.value = detail
    if (detail?.status === 'succeeded' && navigateOnSuccess) {
      navigateOnSuccess = false
      ElMessage.success('任务 #' + detail.id + ' 已成功，正在打开知识管理')
      await router.push('/knowledge')
      return
    }
    scheduleRefresh()
  } catch (cause) {
    if (!disposed && current === revision) error.value = cause instanceof Error ? cause.message : '任务读取失败，请刷新重试'
  } finally { if (!disposed && current === revision) loading.value = false }
}

/** 默认授权为空，候选读取失败明确展示并保留可用的全局授权开关。 */
async function refreshCandidates(): Promise<void> {
  candidateError.value = ''
  try { await loadCandidates() }
  catch (cause) { candidateError.value = cause instanceof Error ? cause.message : '授权候选读取失败' }
}

/** 展示相对路径并拒绝浏览器来源中的绝对路径或目录穿越。 */
function normalizePath(path: string): string {
  const value = path.replace(/\\/g, '/')
  if (!value || value.startsWith('/') || /^[A-Za-z]:/.test(value) || value.split('/').some((part) => !part || part === '.' || part === '..')) throw new Error('无效相对路径：' + path)
  return value
}

/** 相同相对路径只收集一次，避免同一批提交覆盖目录中的同名文件。 */
function appendFiles(entries: { file: File; path: string }[]): void {
  const paths = new Set(files.value.map((entry) => entry.path))
  const additions: { file: File; path: string }[] = []
  let duplicates = 0
  for (const entry of entries) {
    const path = normalizePath(entry.path)
    if (paths.has(path)) { duplicates += 1; continue }
    paths.add(path); additions.push({ file: entry.file, path })
  }
  files.value.push(...additions)
  if (duplicates) ElMessage.warning('已跳过 ' + duplicates + ' 个重复相对路径；如需替换，请先移除原文件')
}

/** 文件与目录选择共用清单，重置输入以支持移除后重新选择同一文件。 */
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  selectionError.value = ''
  try { appendFiles(Array.from(input.files ?? [], (file) => ({ file, path: file.webkitRelativePath || file.name }))) }
  catch (cause) { selectionError.value = cause instanceof Error ? cause.message : '文件选择失败' }
  finally { input.value = '' }
}

/** 包装目录读取回调，每次返回真实的一批子节点。 */
function readDirectoryBatch(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject))
}

/** 递归遍历全部批次，Chromium 每批最多返回部分节点，必须读到空批次。 */
async function collectEntry(entry: FileSystemEntry): Promise<{ file: File; path: string }[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
    return [{ file, path: entry.fullPath.replace(/^\//, '') }]
  }
  if (!entry.isDirectory) return []
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  const result: { file: File; path: string }[] = []
  for (;;) {
    const batch = await readDirectoryBatch(reader)
    if (!batch.length) break
    for (const child of batch) result.push(...await collectEntry(child))
  }
  return result
}

/** 在拖放事件有效期内取得目录句柄，再异步递归读取整个目录。 */
async function dropFiles(event: DragEvent): Promise<void> {
  dragging.value = false
  if (submitting.value || collecting.value || !event.dataTransfer) return
  const items = Array.from(event.dataTransfer.items).filter((item) => item.kind === 'file')
  const sources = items.map((item) => ({ entry: item.webkitGetAsEntry?.() ?? null, file: item.getAsFile() }))
  const fallback = Array.from(event.dataTransfer.files)
  collecting.value = true; selectionError.value = ''
  try {
    const entries: { file: File; path: string }[] = []
    if (sources.length) {
      for (const source of sources) {
        if (source.entry) entries.push(...await collectEntry(source.entry))
        else if (source.file) entries.push({ file: source.file, path: source.file.name })
      }
    } else { for (const file of fallback) entries.push({ file, path: file.webkitRelativePath || file.name }) }
    if (!disposed) {
      if (!entries.length) selectionError.value = '未发现可读取文件；空文件夹不会创建导入任务'
      else appendFiles(entries)
    }
  } catch (cause) { if (!disposed) selectionError.value = cause instanceof Error ? cause.message : '目录读取失败，请使用文件夹选择重试' }
  finally { if (!disposed) collecting.value = false }
}

/** 只发送真实文件及相对路径，提交后跟踪服务端返回的任务。 */
async function submitImport(): Promise<void> {
  if (!files.value.length || submitting.value || collecting.value) return
  submitting.value = true; error.value = ''
  try {
    const task = await api.createImport(files.value, acl.value, category.value)
    if (disposed) return
    files.value = []; selected.value = task; navigateOnSuccess = true
    if (selectedId.value === task.id) await refreshTasks()
    else await router.replace({ path: '/imports', query: { task: task.id } })
  } catch (cause) { if (!disposed) error.value = cause instanceof Error ? cause.message : '导入提交失败' }
  finally { if (!disposed) submitting.value = false }
}

/** 选中历史任务只查看详情，成功任务保留详情便于核对分项。 */
async function selectTask(task: ImportTask): Promise<void> {
  navigateOnSuccess = false
  if (selectedId.value === task.id) { await refreshTasks(); return }
  selected.value = null
  await router.replace({ path: '/imports', query: { task: task.id } })
}

/** 显式重试仅由后端重排失败项，成功项不会再次提交。 */
async function retryTask(): Promise<void> {
  if (!selected.value || retrying.value || !selected.value.failed || taskIsPending(selected.value.status)) return
  retrying.value = true; error.value = ''
  stopPolling(); revision += 1
  const id = selected.value.id
  try {
    const task = await api.retryImport(id)
    if (disposed || selectedId.value !== id) return
    selected.value = task
    navigateOnSuccess = true
    await refreshTasks()
  } catch (cause) { if (!disposed) error.value = cause instanceof Error ? cause.message : '重试失败' }
  finally { if (!disposed) retrying.value = false }
}

/** 将字节数量转为便于核对的大小，不推测解析后的文档体积。 */
function fileSize(bytes: number): string { return bytes < 1024 ? bytes + ' B' : bytes < 1024 * 1024 ? (bytes / 1024).toFixed(1) + ' KB' : (bytes / 1024 / 1024).toFixed(1) + ' MB' }

watch(selectedId, () => { selected.value = null; void refreshTasks() })
watch(() => session.user?.permission_version, () => { void refreshCandidates() })
onMounted(() => { void refreshTasks(); void refreshCandidates() })
onBeforeUnmount(() => { disposed = true; revision += 1; stopPolling(); files.value = []; selected.value = null })
</script>

<template>
  <PageHeader title="知识导入中心" description="选择单文件、批量文件或整个文件夹；解析、切片与索引进度以后台任务实际状态为准。">
    <el-button :loading="loading" :disabled="submitting || retrying" @click="refreshTasks">刷新任务</el-button>
    <el-button @click="router.push('/knowledge')">知识管理</el-button>
  </PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <section class="surface">
    <h3>选择文档</h3>
    <div class="import-dropzone" :class="{ dragging }" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="dropFiles">
      <strong>{{ collecting ? '正在读取目录中的全部文件…' : '将文件或文件夹拖放到这里' }}</strong>
      <p>支持 PDF、DOCX、DOC、Markdown 和 TXT；旧 DOC 需要服务端已安装转换工具。</p>
      <div class="inline-actions"><el-button :disabled="submitting || collecting" @click="singleInput?.click()">单文件</el-button><el-button :disabled="submitting || collecting" @click="batchInput?.click()">批量选择</el-button><el-button :disabled="submitting || collecting" @click="folderInput?.click()">选择文件夹</el-button></div>
      <input ref="singleInput" hidden type="file" @change="chooseFiles" />
      <input ref="batchInput" hidden type="file" multiple @change="chooseFiles" />
      <input ref="folderInput" hidden type="file" multiple webkitdirectory @change="chooseFiles" />
    </div>
    <el-alert v-if="selectionError" :title="selectionError" type="error" :closable="false" class="page-alert" />
    <div v-if="files.length" class="surface-heading"><span>已选择 {{ files.length }} 个文件</span><el-button text :disabled="submitting || collecting" @click="files = []">清空列表</el-button></div>
    <el-table :data="files" empty-text="尚未选择文件" max-height="330">
      <el-table-column prop="path" label="相对路径" min-width="280" />
      <el-table-column label="大小" width="120"><template #default="{ row }">{{ fileSize(row.file.size) }}</template></el-table-column>
      <el-table-column label="操作" width="85"><template #default="{ $index }"><el-button link type="danger" :disabled="submitting || collecting" @click="files.splice($index, 1)">移除</el-button></template></el-table-column>
    </el-table>
    <el-form label-position="top" class="import-settings" :disabled="submitting">
      <el-form-item label="分类（可选）"><el-input v-model="category" placeholder="例如：操作手册" maxlength="100" /></el-form-item>
      <h3>初始访问范围</h3>
      <el-alert v-if="candidateError" :title="candidateError" type="warning" :closable="false" class="page-alert"><el-button link @click="refreshCandidates">重新读取候选</el-button></el-alert>
      <AclEditor v-model="acl" :departments="departments" :roles="roles" :users="users" :can-read-departments="canReadDepartments" :can-read-roles="canReadRoles" :can-read-users="canReadUsers" :disabled="submitting" />
      <el-button type="primary" :loading="submitting" :disabled="!files.length || collecting" @click="submitImport">提交 {{ files.length }} 个文件</el-button>
    </el-form>
  </section>
  <section class="surface" v-loading="loading && !tasks.length">
    <div class="surface-heading"><h3>导入与重建任务 <span class="count-badge">{{ tasks.length }}</span></h3><span class="muted">排队与运行中的任务自动刷新</span></div>
    <el-table :data="tasks" empty-text="暂无导入或重建任务" highlight-current-row @row-click="selectTask">
      <el-table-column prop="id" label="任务 ID" width="100" />
      <el-table-column label="状态" min-width="120"><template #default="{ row }">{{ taskStatusLabel(row.status) }}</template></el-table-column>
      <el-table-column prop="total" label="文件总数" width="100" />
      <el-table-column prop="completed" label="已完成" width="100" />
      <el-table-column prop="failed" label="失败" width="85" />
      <el-table-column label="详情" width="100"><template #default="{ row }"><el-button link type="primary" @click.stop="selectTask(row)">查看任务</el-button></template></el-table-column>
    </el-table>
  </section>
  <section v-if="selected" class="surface">
    <div class="surface-heading"><h3>任务 #{{ selected.id }} · {{ taskStatusLabel(selected.status) }}</h3><div class="inline-actions"><el-button v-if="selected.status === 'succeeded' || selected.status === 'partial'" @click="router.push('/knowledge')">查看知识管理</el-button><el-button v-if="selected.failed > 0" type="warning" :loading="retrying" :disabled="taskIsPending(selected.status) || submitting" @click="retryTask">仅重试失败项（{{ selected.failed }}）</el-button></div></div>
    <p class="field-hint">共 {{ selected.total }} 项，已完成 {{ selected.completed }} 项，失败 {{ selected.failed }} 项。进度由后台返回；失败后需要显式重试。</p>
    <el-table :data="selected.items" empty-text="该任务暂未返回分项">
      <el-table-column prop="relative_path" label="相对路径 / 重建对象" min-width="220" />
      <el-table-column label="状态 / 阶段" min-width="150"><template #default="{ row }">{{ taskStatusLabel(row.status) }}<div class="field-hint">{{ taskStageLabel(row.stage) }}</div></template></el-table-column>
      <el-table-column label="实际进度" min-width="160"><template #default="{ row }"><el-progress :percentage="row.progress" :status="row.status === 'failed' ? 'exception' : row.status === 'succeeded' ? 'success' : undefined" /></template></el-table-column>
      <el-table-column prop="attempt" label="尝试次数" width="95" />
      <el-table-column label="知识 / 版本" min-width="115"><template #default="{ row }">{{ row.knowledge_id ?? '—' }} / {{ row.version_id ?? '—' }}</template></el-table-column>
      <el-table-column label="失败原因" min-width="230"><template #default="{ row }"><span class="error-text">{{ row.error || '—' }}</span></template></el-table-column>
    </el-table>
  </section>
</template>
