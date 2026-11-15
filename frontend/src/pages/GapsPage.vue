<!-- 知识缺口作用域列表与补充任务真实导入闭环。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { Gap, Supplement } from '../types'
const gaps = ref<Gap[]>([]), tasks = ref<Supplement[]>([]), error = ref(''), busy = ref(false)
const router = useRouter()
/** 获取当前身份可管理的缺口和明确受指派的任务。 */
async function refresh(): Promise<void> {
  busy.value = true; error.value = ''
  try { [gaps.value, tasks.value] = await Promise.all([request<Gap[]>('/gaps'), request<Supplement[]>('/supplement-tasks')]) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' }
  finally { busy.value = false }
}
/** 对同一未完成缺口幂等创建补充任务，默认当前人为责任人。 */
async function createTask(row: Gap): Promise<void> {
  try { await request<Supplement>('/gaps/' + row.id + '/tasks', 'POST', {}); await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '转任务失败' }
}
onMounted(refresh)
</script>
<template>
  <PageHeader title="知识缺口与补充" description="无召回或已验证的低证据问题进入缺口池；权限拦截和模型服务错误单独记录。"><el-button :loading="busy" @click="refresh">刷新</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" />
  <section class="surface"><h3>待补充知识</h3><el-table :data="gaps" empty-text="暂无当前作用域的知识缺口"><el-table-column prop="question" label="问题" min-width="260" /><el-table-column prop="frequency" label="频次" width="80" /><el-table-column prop="status" label="状态" width="110" /><el-table-column label="操作" width="160"><template #default="{ row }"><el-button link type="primary" :disabled="row.status === 'resolved'" @click="createTask(row)">转为补充任务</el-button></template></el-table-column></el-table></section>
  <section class="surface"><h3>补充任务</h3><el-table :data="tasks" empty-text="暂无补充任务"><el-table-column prop="id" label="编号" width="75" /><el-table-column prop="title" label="标题" min-width="240" /><el-table-column prop="assigned_to" label="责任人ID" width="100" /><el-table-column prop="status" label="状态" width="110" /><el-table-column label="关联知识" min-width="120"><template #default="{ row }">{{ row.knowledge_ids.join('、') || '尚未发布' }}</template></el-table-column><el-table-column label="操作" width="120"><template #default="{ row }"><el-button link type="primary" :disabled="row.status !== 'open'" @click="router.push('/imports?supplement=' + row.id)">导入补充资料</el-button></template></el-table-column></el-table><p class="field-hint">关联导入任务全部成功发布后，任务与缺口才会自动标记完成。</p></section>
</template>
