<!-- 独立审计权限页面，不与普通问答或运营排行共用受限候选明细。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { Audit } from '../types'
const rows = ref<Audit[]>([]), error = ref(''), busy = ref(false)
/** 仅调用有audit:read检查的独立入口。 */
async function refresh(): Promise<void> { busy.value = true; error.value = ''; try { rows.value = await request<Audit[]>('/audits') } catch (cause) { error.value = cause instanceof Error ? cause.message : '审计读取失败' } finally { busy.value = false } }
onMounted(refresh)
</script>
<template><PageHeader title="问答安全审计" description="最近200次请求，包含独立授权的候选、放行与拦截ID及服务失败原因。"><el-button :loading="busy" @click="refresh">刷新</el-button></PageHeader><el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" /><section class="surface"><el-table :data="rows" empty-text="暂无真实问答审计"><el-table-column type="expand"><template #default="{ row }"><div class="audit-detail"><p>request_id：{{ row.request_id }}</p><p>候选：{{ row.candidates.join('、') || '无' }}</p><p>放行：{{ row.allowed.join('、') || '无' }}</p><p>拦截：{{ row.blocked.join('、') || '无' }}</p><p>调用模型：{{ row.model_called ? '是' : '否' }}；Token：{{ row.token_usage ? JSON.stringify(row.token_usage) : '未知或未调用' }}</p><p>耗时：{{ row.latency_ms ?? '未知' }} ms；错误码：{{ row.error_code || '无' }}</p></div></template></el-table-column><el-table-column prop="user_id" label="用户ID" width="90" /><el-table-column prop="question" label="问题" min-width="260" /><el-table-column prop="source_mode" label="来源模式" width="120" /><el-table-column prop="status" label="状态" width="120" /></el-table></section></template>
