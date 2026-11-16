<!-- 私人会话工作台，消费真实SSE并在错误、取消或撤权时清除未完成答案。 -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeader from '../components/PageHeader.vue'
import { askStream, request } from '../api'
import { renderMarkdown } from '../markdown'
import type { ChatMessage, Citation, Conversation } from '../types'

const conversations = ref<Conversation[]>([]), selected = ref<number | null>(null), messages = ref<ChatMessage[]>([])
const question = ref(''), error = ref(''), running = ref(false), restricted = ref(false), preview = ref<Citation | null>(null)
const usage = ref(''), model = ref(''), title = ref('新会话'), loading = ref(false)
let controller: AbortController | null = null, revision = 0, disposed = false
const suggestions = computed(() => [...new Set(messages.value.filter((row) => row.role === 'user' && row.content.includes(question.value)).map((row) => row.content))].slice(-5))

/** 重读本人列表，不保留已删除的会话正文。 */
async function refresh(): Promise<void> { conversations.value = await request<Conversation[]>('/conversations') }
/** 切换前取消旧流并销毁旧正文，迟到响应不再覆盖新会话。 */
async function selectConversation(id: number): Promise<void> {
  cancel(); const current = ++revision; selected.value = id; messages.value = []; preview.value = null; error.value = ''; usage.value = ''; model.value = ''; restricted.value = false
  loading.value = true
  try { const rows = await request<ChatMessage[]>('/conversations/' + id + '/messages'); if (current === revision && !disposed) { messages.value = rows; title.value = conversations.value.find((row) => row.id === id)?.title ?? '新会话' } }
  catch (cause) { if (current === revision) error.value = cause instanceof Error ? cause.message : '会话读取失败' }
  finally { if (current === revision) loading.value = false }
}
/** 新建私人会话，不生成演示消息。 */
async function create(): Promise<void> {
  try { const row = await request<Conversation>('/conversations', 'POST', { title: '新会话' }); await refresh(); await selectConversation(row.id) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '创建失败' }
}
/** 会话标题通过本人受控接口修改。 */
async function rename(): Promise<void> {
  if (!selected.value) return
  try { await request('/conversations/' + selected.value, 'PATCH', { title: title.value }); await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '修改失败' }
}
/** 用户明确点击删除后才移除会话。 */
async function remove(): Promise<void> {
  if (!selected.value) return
  try { await ElMessageBox.confirm('删除当前会话及其消息？', '删除会话', { type: 'warning' }); cancel(); await request('/conversations/' + selected.value, 'DELETE'); selected.value = null; messages.value = []; model.value = ''; usage.value = ''; await refresh() }
  catch (cause) { if (cause instanceof Error) error.value = cause.message }
}
/** 取消时立即隐藏未完成正文，上游连接由AbortSignal关闭。 */
function cancel(): void { controller?.abort(); controller = null; running.value = false; for (const row of messages.value) if (row.status === 'running') { row.content = ''; row.citations = []; row.status = 'cancelled' } }
/** 根据每条消息保存的来源和模型名展示标识，历史重载后仍保持明确。 */
function answerLabel(message: ChatMessage): string {
  if (message.source_mode === 'external_api') return '外部 API 联调 · ' + (message.model_version || '模型名称未返回')
  if (message.source_mode === 'faq') return '已发布 FAQ'
  if (message.model_version) return '正式微调模型 · ' + message.model_version
  return '来源：' + (message.source_mode || '等待模型信息')
}
/** 发送真实问题；收到error不会保留流式草稿或成功状态。 */
async function send(): Promise<void> {
  if (running.value || !question.value.trim()) return
  if (!selected.value) { await create(); if (!selected.value) return }
  const current = revision, id = selected.value, text = question.value.trim()
  const abort = new AbortController(); controller = abort; running.value = true; error.value = ''; usage.value = ''; model.value = ''; restricted.value = false
  question.value = ''
  messages.value.push({ id: -1, role: 'user', content: text, status: 'complete', citations: [], model_version: null, source_mode: null })
  messages.value.push({ id: -2, role: 'assistant', content: '', status: 'running', citations: [], model_version: null, source_mode: null })
  const answer = messages.value[messages.value.length - 1]!
  try {
    await askStream(id, text, abort.signal, (name, data) => {
      if (current !== revision || disposed) return
      if (name === 'meta') { restricted.value = Boolean(data.restricted); answer.id = Number(data.message_id); answer.model_version = data.model_version ? String(data.model_version) : null; answer.source_mode = data.source_mode ? String(data.source_mode) : null; model.value = answerLabel(answer) }
      if (name === 'delta') answer.content += String(data.text ?? '')
      if (name === 'citations') answer.citations = data.items as Citation[]
      if (name === 'usage') usage.value = data.known ? JSON.stringify(data.tokens) + ' · ' + Number(data.latency_ms).toFixed(0) + ' ms' : '模型未返回Token用量'
      if (name === 'done') answer.status = 'complete'
      if (name === 'error') { answer.content = ''; answer.citations = []; answer.status = 'error'; error.value = String(data.detail ?? '问答失败') }
    })
  } catch (cause) { if (current === revision) { answer.content = ''; answer.citations = []; answer.status = abort.signal.aborted ? 'cancelled' : 'error'; if (!abort.signal.aborted) error.value = cause instanceof Error ? cause.message : '问答连接失败' } }
  finally { if (current === revision) { running.value = false; controller = null; await refresh().catch(() => undefined) } }
}
/** 引用每次打开时重新向服务端鉴权，失败立即清除原预览。 */
async function openCitation(message: ChatMessage, citation: Citation): Promise<void> {
  preview.value = null
  try { preview.value = await request<Citation>('/citations/' + message.id + '/' + encodeURIComponent(citation.label)) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '引用无法读取'; if (selected.value) await selectConversation(selected.value) }
}
/** 复制安全渲染代码文本，不执行代码或HTML。 */
async function copyCode(event: MouseEvent): Promise<void> {
  const target = (event.target as Element).closest('[data-copy-code]')
  if (!target) return
  try { await navigator.clipboard.writeText(target.parentElement?.querySelector('code')?.textContent ?? ''); ElMessage.success('代码已复制') }
  catch { ElMessage.error('浏览器未授予剪贴板权限') }
}
/** 页面恢复焦点时重新核对历史来源。 */
function revalidate(): void { if (!running.value && selected.value) void selectConversation(selected.value) }
onMounted(async () => { window.addEventListener('focus', revalidate); try { await refresh() } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' } })
onBeforeUnmount(() => { disposed = true; revision++; cancel(); messages.value = []; preview.value = null; window.removeEventListener('focus', revalidate) })
</script>

<template>
  <PageHeader title="AI 智能问答" description="根据当前账号获准资料作答；每轮重新检查来源与历史权限。"><el-button @click="create">新建会话</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" />
  <div class="chat-layout">
    <aside class="surface conversation-list"><h3>历史会话</h3><el-empty v-if="!conversations.length" description="暂无会话" /><el-button v-for="row in conversations" :key="row.id" :type="row.id === selected ? 'primary' : 'default'" @click="selectConversation(row.id)">{{ row.title }}</el-button></aside>
    <section class="surface chat-main" v-loading="loading">
      <div v-if="selected" class="inline-actions"><el-input v-model="title" aria-label="会话标题" maxlength="200" /><el-button :disabled="running" @click="rename">保存标题</el-button><el-button type="danger" plain :disabled="running" @click="remove">删除会话</el-button></div>
      <el-empty v-if="!messages.length" description="输入问题，开始基于资料的对话" />
      <article v-for="(row, index) in messages" :key="index" class="chat-message" :class="row.role">
        <strong>{{ row.role === 'user' ? '我' : '知识助手' }}</strong>
        <p v-if="row.role === 'assistant'" class="field-hint">{{ answerLabel(row) }}</p>
        <p v-if="row.status === 'unavailable'" class="muted">来源权限或版本已失效，历史答案不可用。</p>
        <p v-else-if="row.status === 'error'" class="muted">本轮失败，未生成有效答案。</p>
        <p v-else-if="row.status === 'cancelled'" class="muted">本轮已中止。</p>
        <div v-else-if="row.role === 'assistant'" class="markdown-content" @click="copyCode" v-html="renderMarkdown(row.content)" />
        <p v-else class="document-content">{{ row.content }}</p>
        <div class="inline-actions"><el-button v-for="source in row.citations" :key="source.label" link type="primary" @click="openCitation(row, source)">[{{ source.label }}] {{ source.title }}</el-button></div>
      </article>
      <el-alert v-if="restricted" title="部分参考资料因权限受限无法展示" type="warning" :closable="false" />
      <p v-if="usage" class="field-hint">{{ model }} · {{ usage }}</p>
      <form class="chat-composer" @submit.prevent="send"><label for="chat-question">提问</label><input id="chat-question" v-model="question" list="question-suggestions" maxlength="4000" :disabled="running" placeholder="请输入问题" autocomplete="off" /><datalist id="question-suggestions"><option v-for="item in suggestions" :key="item" :value="item" /></datalist><el-button v-if="running" type="warning" @click="cancel">停止回答</el-button><el-button v-else native-type="submit" type="primary" :disabled="!question.trim()">发送</el-button></form>
    </section>
  </div>
  <el-dialog :model-value="preview !== null" title="引用来源" width="720px" @close="preview = null"><template v-if="preview"><h3>{{ preview.title }}</h3><p class="document-meta">版本 {{ preview.version_id }} · {{ preview.location }}</p><div class="document-content">{{ preview.content }}</div></template></el-dialog>
</template>
