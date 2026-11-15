<!-- 真实问答用量、覆盖率及权限拦截运营面板。 -->
<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { Metrics } from '../types'
echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const data = ref<Metrics | null>(null), error = ref(''), days = ref(7), busy = ref(false), chartElement = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined, disposed = false
/** 从同一真实请求集展示数值，不填充演示流量。 */
async function refresh(): Promise<void> {
  busy.value = true; error.value = ''
  try {
    const value = await request<Metrics>('/metrics?days=' + days.value)
    if (disposed) return
    data.value = value; await nextTick()
    if (chartElement.value) { chart ??= echarts.init(chartElement.value); chart.setOption({ tooltip: { trigger: 'axis' }, legend: { data: ['问答PV', '独立用户UV', 'Token'] }, grid: { left: 55, right: 30, bottom: 40 }, xAxis: { type: 'category', data: value.daily.map((row) => row.date) }, yAxis: { type: 'value', minInterval: 1 }, series: ['pv', 'uv', 'tokens'].map((key, index) => ({ name: ['问答PV', '独立用户UV', 'Token'][index], type: 'line', data: value.daily.map((row) => row[key as 'pv' | 'uv' | 'tokens']) })) }) }
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '统计读取失败' }
  finally { busy.value = false }
}
/** 格式化真实分位数，空数据不显示虚构的零延时。 */
function milliseconds(value: number | null): string { return value === null ? '暂无样本' : value.toFixed(1) + ' ms' }
/** 触发真实worker快照聚合。 */
async function aggregate(): Promise<void> { try { await request('/metrics/aggregate', 'POST'); await refresh() } catch (cause) { error.value = cause instanceof Error ? cause.message : '聚合提交失败' } }
/** 容器宽度变化时重新布局。 */
function resize(): void { chart?.resize() }
onMounted(() => { window.addEventListener('resize', resize); void refresh() })
onBeforeUnmount(() => { disposed = true; window.removeEventListener('resize', resize); chart?.dispose() })
</script>
<template>
  <PageHeader title="运营看板" description="有效问答请求包括拒答、权限拦截、FAQ和服务失败；未知Token独立统计。"><el-input-number v-model="days" :min="1" :max="90" aria-label="统计天数" /><el-button :loading="busy" @click="refresh">刷新统计</el-button><el-button @click="aggregate">生成每日快照</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" />
  <template v-if="data"><section class="metric-grid"><div class="surface"><span>问答 PV / UV</span><h2>{{ data.pv }} / {{ data.uv }}</h2></div><div class="surface"><span>当前可读知识</span><h2>{{ data.knowledge_count }}</h2></div><div class="surface"><span>已知 Token</span><h2>{{ data.token_total }}</h2><small>未知用量 {{ data.token_unknown_requests }} 次</small></div><div class="surface"><span>失败 / 中止</span><h2>{{ data.failures }}</h2></div></section><section class="surface"><div class="inline-actions"><el-tag>FAQ命中 {{ (data.faq_hit_rate * 100).toFixed(1) }}%</el-tag><el-tag>知识覆盖 {{ (data.coverage_rate * 100).toFixed(1) }}%</el-tag><el-tag type="warning">权限拦截 {{ (data.permission_block_rate * 100).toFixed(1) }}%</el-tag></div><p>平均 {{ milliseconds(data.latency.mean_ms) }} · P50 {{ milliseconds(data.latency.p50_ms) }} · P95 {{ milliseconds(data.latency.p95_ms) }}</p><div ref="chartElement" class="metrics-chart" /></section><section class="surface"><h3>当前作用域高频问题</h3><el-table :data="data.top_questions" empty-text="暂无可显示的问题排行"><el-table-column prop="question" label="问题" /><el-table-column prop="count" label="次数" width="100" /></el-table></section><section class="surface"><h3>当前获准来源热度</h3><el-table :data="data.top_knowledge" empty-text="暂无获准引用"><el-table-column prop="title" label="知识" /><el-table-column prop="count" label="引用次数" width="120" /></el-table></section></template>
</template>
