<!-- 调用真实混合检索接口，仅展示后端获准返回的片段与来源。 -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import type { SearchResponse } from '../types'
import { useSessionStore } from '../stores/session'
import PageHeader from '../components/PageHeader.vue'

const session = useSessionStore()
const query = ref('')
const limit = ref(8)
const loading = ref(false)
const error = ref('')
const result = ref<SearchResponse | null>(null)
const searchedQuery = ref('')
let revision = 0
let disposed = false

/** 每次检索先销毁旧正文，真实 API 独立校验身份和候选 ACL。 */
async function search(): Promise<void> {
  if (loading.value || !query.value.trim()) return
  const current = ++revision
  const text = query.value.trim()
  result.value = null; searchedQuery.value = ''; error.value = ''; loading.value = true
  try {
    await session.refresh()
    if (!session.can('knowledge:read')) throw new ApiError(403, '权限不足：当前账号没有知识读取权限')
    if (disposed || current !== revision) return
    const response = await api.search(text, Math.min(8, Math.max(1, limit.value || 8)))
    if (!disposed && current === revision) { result.value = response; searchedQuery.value = text }
  } catch (cause) { if (!disposed && current === revision) error.value = cause instanceof Error ? cause.message : '检索失败，请重试' }
  finally { if (!disposed && current === revision) loading.value = false }
}

/** 身份变更或离开页面时清除曾获准的正文，迟到响应不再显示。 */
function clearResults(): void { revision += 1; result.value = null; searchedQuery.value = ''; loading.value = false }
watch(() => session.user?.permission_version, clearResults)
watch(() => session.user?.permissions, () => { if (!session.can('knowledge:read')) clearResults() })
onBeforeUnmount(() => { disposed = true; clearResults() })
</script>

<template>
  <PageHeader title="知识检索" description="检索已启用知识的活动版本，展示当前身份获准读取的来源片段与真实排序分数。" />
  <section class="surface">
    <el-form class="search-form" @submit.prevent="search">
      <el-form-item label="检索问题" class="search-query"><el-input v-model="query" clearable placeholder="输入术语、操作步骤或问题" aria-label="检索问题" :disabled="loading" /></el-form-item>
      <el-form-item label="最多片段"><el-input-number v-model="limit" :min="1" :max="8" :step="1" :precision="0" :disabled="loading" aria-label="最多返回片段数量" /></el-form-item>
      <el-form-item><el-button type="primary" native-type="submit" :loading="loading" :disabled="!query.trim()">检索</el-button></el-form-item>
    </el-form>
    <p class="field-hint">结果来自真实向量与关键词检索、权限过滤及重排；当前页面展示资料片段。</p>
  </section>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="page-alert" />
  <el-alert v-if="result?.restricted" title="部分参考资料因权限受限无法展示" type="warning" :closable="false" show-icon class="page-alert" />
  <div v-if="loading" class="surface" v-loading="loading"><p class="muted">正在检索获准资料…</p></div>
  <template v-else-if="result">
    <div class="surface-heading"><h3>“{{ searchedQuery }}” 的获准结果</h3><span class="muted">{{ result.items.length }} 个可读片段</span></div>
    <section v-if="!result.items.length" class="surface"><el-empty :description="result.restricted ? '没有获准的可用资料，无法依据当前可见资料作答' : '未找到获准的相关资料，请调整关键词或补充知识'" /></section>
    <article v-for="item in result.items" :key="item.chunk_id" class="surface search-result">
      <div class="surface-heading"><h3>{{ item.title }}</h3><el-tag type="info">检索分数 {{ item.score }}</el-tag></div>
      <div class="document-meta">知识 #{{ item.knowledge_id }} · 版本 #{{ item.version_id }} · 切片 #{{ item.chunk_id }}<span v-if="item.section"> · {{ item.section }}</span><span v-if="item.page !== null"> · 第 {{ item.page }} 页</span><span v-if="item.location"> · {{ item.location }}</span></div>
      <div class="document-content">{{ item.content }}</div>
    </article>
  </template>
  <section v-else-if="!error && !loading" class="surface"><el-empty description="输入问题，检索当前账号获准读取的知识" /></section>
</template>
