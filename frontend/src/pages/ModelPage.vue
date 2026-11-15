<!-- 只写凭证的模型设置页，展示真实产物与校准检查状态。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { ModelSettings } from '../types'
const settings = ref<ModelSettings | null>(null), error = ref(''), busy = ref(false), saved = ref(false)
const form = ref({ base_url: '', model_name: 'kb-grounded-qwen3-4b-v1', api_key: '', release_manifest_path: '', calibration_manifest_path: '' })
/** 只读状态不返回密钥和本机文件路径。 */
async function refresh(): Promise<void> { try { settings.value = await request<ModelSettings>('/settings/model'); form.value.base_url = settings.value.base_url; form.value.model_name = settings.value.model_name } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' } }
/** 空凭证表示保留已存密钥，提交后立即清除页面内存中的新凭证。 */
async function save(): Promise<void> {
  busy.value = true; error.value = ''; saved.value = false
  try { settings.value = await request<ModelSettings>('/settings/model', 'PUT', { ...form.value, api_key: form.value.api_key || undefined, release_manifest_path: form.value.release_manifest_path || null, calibration_manifest_path: form.value.calibration_manifest_path || null }); saved.value = true }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '保存失败' }
  finally { form.value.api_key = ''; busy.value = false }
}
onMounted(refresh)
</script>
<template><PageHeader title="模型服务设置" description="正式生成模型固定为微调项目发布的 kb-grounded-qwen3-4b-v1。" /><el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" /><el-alert v-if="saved" title="配置已保存，生成能力仍以发布与校准检查结果为准" type="success" :closable="false" class="page-alert" /><section class="surface"><template v-if="settings"><h3>{{ settings.status }}</h3><p>发布产物：{{ settings.release_verified ? '已验证' : '未验证' }} · 检索校准：{{ settings.calibration_verified ? '已验证' : '未验证' }} · 密钥：{{ settings.has_api_key ? '已设置' : '未设置' }}</p><p class="field-hint">检索配置标识：{{ settings.configuration_id }}</p></template><el-form label-position="top" @submit.prevent="save"><el-form-item label="模型服务地址"><el-input v-model="form.base_url" placeholder="http://127.0.0.1:8000" /></el-form-item><el-form-item label="发布模型别名"><el-input v-model="form.model_name" readonly /></el-form-item><el-form-item label="服务密钥（留空保留原密钥）"><el-input v-model="form.api_key" type="password" autocomplete="new-password" /></el-form-item><el-form-item label="发布清单文件路径（服务端可访问）"><el-input v-model="form.release_manifest_path" placeholder="release-manifest.json 的绝对路径" /></el-form-item><el-form-item label="人工审核的独立检索校准报告路径"><el-input v-model="form.calibration_manifest_path" /></el-form-item><p class="field-hint">首次设置密钥前，服务端须提供独立 CONFIG_ENCRYPTION_KEY。填写清单后会校验文件摘要和质量门槛；尚未交付真实微调产物时，生成问答明确失败。</p><el-button native-type="submit" type="primary" :loading="busy">验证并保存</el-button></el-form></section></template>
