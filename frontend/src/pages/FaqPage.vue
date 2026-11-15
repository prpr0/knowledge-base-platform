<!-- 高频FAQ候选审核、真实挖掘状态与不可变发布版本控制。 -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { Candidate, FaqRelease, MiningRun } from '../types'
const candidates = ref<Candidate[]>([]), releases = ref<FaqRelease[]>([]), run = ref<MiningRun | null>(null)
const error = ref(''), busy = ref(false), editing = ref<Candidate | null>(null), question = ref(''), answer = ref(''), aliases = ref('')
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false
/** 列表来自真实后台，只展示当前身份获准的候选和来源。 */
async function refresh(): Promise<void> {
  busy.value = true; error.value = ''
  try { const values = await Promise.all([request<Candidate[]>('/faq/candidates'), request<FaqRelease[]>('/faq/releases')]); if (!disposed) [candidates.value, releases.value] = values }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' }
  finally { busy.value = false }
}
/** 轮询数据库终态，不把发起成功当作聚类完成。 */
async function poll(): Promise<void> {
  if (!run.value || disposed) return
  try { run.value = await request<MiningRun>('/faq/mining/' + run.value.id); if (['queued', 'running'].includes(run.value.status)) timer = setTimeout(poll, 2000); else await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取任务失败' }
}
/** 发起真实七天高频聚类任务。 */
async function mine(): Promise<void> {
  error.value = ''
  try { run.value = await request<MiningRun>('/faq/mine', 'POST'); await poll() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '发起失败' }
}
/** 打开人工润色表单，保留全部来源。 */
function edit(row: Candidate): void { editing.value = row; question.value = row.question; answer.value = row.answer; aliases.value = row.aliases.join('\n') }
/** 审核前候选不会参与问答；发布由后端再次检查所有来源。 */
async function review(action: 'publish' | 'reject'): Promise<void> {
  if (!editing.value) return
  busy.value = true
  try { await request('/faq/candidates/' + editing.value.id + '/review', 'POST', { action, question: question.value, answer: answer.value, aliases: aliases.value.split('\n').map((item) => item.trim()).filter(Boolean), sources: editing.value.sources }); editing.value = null; await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '审核失败' }
  finally { busy.value = false }
}
/** 启停版本不修改原答案，失效来源不能重新启用。 */
async function toggle(row: FaqRelease): Promise<void> {
  try { await request('/faq/releases/' + row.id, 'PATCH', { enabled: !row.enabled }); await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '状态修改失败' }
}
onMounted(refresh)
onBeforeUnmount(() => { disposed = true; if (timer) clearTimeout(timer) })
</script>
<template>
  <PageHeader title="FAQ 挖掘与审核" description="7天内同簇问题至少5次形成候选；人工审核发布后才用于问答。"><el-button :disabled="run?.status === 'queued' || run?.status === 'running'" @click="mine">挖掘高频问题</el-button><el-button :loading="busy" @click="refresh">刷新</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" />
  <el-alert v-if="run" :title="'挖掘任务：' + run.status + '；新增候选：' + run.count + (run.error ? '；' + run.error : '')" :type="run.status === 'failed' ? 'error' : 'info'" :closable="false" class="page-alert" />
  <section class="surface"><h3>待审与已审核候选</h3><el-table :data="candidates" empty-text="暂无当前账号可访问的候选"><el-table-column prop="question" label="代表问题" min-width="240" /><el-table-column prop="count" label="频次" width="80" /><el-table-column prop="confidence" label="簇内相似度" width="130" /><el-table-column prop="status" label="状态" width="120" /><el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" :disabled="row.status !== 'pending'" @click="edit(row)">审核</el-button></template></el-table-column></el-table></section>
  <section class="surface"><h3>发布版本</h3><el-table :data="releases" empty-text="暂无有效来源的发布版本"><el-table-column prop="question" label="标准问题" min-width="220" /><el-table-column prop="version" label="版本" width="80" /><el-table-column label="状态" width="100"><template #default="{ row }">{{ row.enabled ? '启用' : '停用' }}</template></el-table-column><el-table-column label="操作" width="100"><template #default="{ row }"><el-button link @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button></template></el-table-column></el-table></section>
  <el-dialog :model-value="editing !== null" title="人工审核与发布" width="800px" @close="editing = null"><el-form label-position="top"><el-form-item label="标准问题"><el-input v-model="question" maxlength="4000" /></el-form-item><el-form-item label="标准答案（每项结论保留引用编号）"><el-input v-model="answer" type="textarea" :rows="12" maxlength="30000" /></el-form-item><el-form-item label="同义提问（每行一条）"><el-input v-model="aliases" type="textarea" :rows="3" /></el-form-item><p v-for="source in editing?.sources" :key="source.label" class="field-hint">[{{ source.label }}] {{ source.title }} · 版本 {{ source.version_id }} · {{ source.location }}</p></el-form><template #footer><el-button :disabled="busy" @click="review('reject')">拒绝候选</el-button><el-button type="primary" :loading="busy" @click="review('publish')">审核并发布</el-button></template></el-dialog>
</template>
