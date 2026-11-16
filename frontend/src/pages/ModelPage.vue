<!-- 只写凭证的模型设置页，展示真实产物与校准检查状态。 -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import PageHeader from '../components/PageHeader.vue'
import { request } from '../api'
import type { ModelSettings } from '../types'
const settings = ref<ModelSettings | null>(null), error = ref(''), busy = ref(false), saved = ref(false)
const form = ref<{ mode: ModelSettings['mode']; base_url: string; model_name: string; api_key: string; release_manifest_path: string; calibration_manifest_path: string }>({ mode: 'finetuned', base_url: '', model_name: 'kb-grounded-qwen3-4b-v1', api_key: '', release_manifest_path: '', calibration_manifest_path: '' })
/** 切换模式时恢复正式发布别名，外部联调允许填写供应商模型名。 */
function changeMode(): void { form.value.model_name = form.value.mode === 'finetuned' ? 'kb-grounded-qwen3-4b-v1' : ''; form.value.release_manifest_path = ''; form.value.calibration_manifest_path = '' }
/** 读取模式与服务信息，不读回密钥和本机文件路径。 */
async function refresh(): Promise<void> { try { settings.value = await request<ModelSettings>('/settings/model'); form.value.mode = settings.value.mode ?? 'finetuned'; form.value.base_url = settings.value.base_url; form.value.model_name = form.value.mode === 'finetuned' ? 'kb-grounded-qwen3-4b-v1' : settings.value.model_name } catch (cause) { error.value = cause instanceof Error ? cause.message : '读取失败' } }
/** 空凭证表示保留已存密钥，提交后立即清除页面内存中的新凭证。 */
async function save(): Promise<void> {
  busy.value = true; error.value = ''; saved.value = false
  try { settings.value = await request<ModelSettings>('/settings/model', 'PUT', { ...form.value, api_key: form.value.api_key || undefined, release_manifest_path: form.value.release_manifest_path || null, calibration_manifest_path: form.value.calibration_manifest_path || null }); saved.value = true }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '保存失败' }
  finally { form.value.api_key = ''; busy.value = false }
}
onMounted(refresh)
</script>
<template>
  <PageHeader title="模型服务设置" description="选择正式微调模型或外部 API 联调；外部联调不代表正式发布验证。" />
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="page-alert" />
  <el-alert v-if="saved" title="配置已保存，请查看当前模式与验证状态" type="success" :closable="false" class="page-alert" />
  <section class="surface">
    <template v-if="settings">
      <h3>{{ settings.status }}</h3>
      <p>当前模式：{{ settings.mode === 'external_api' ? '外部 API 联调' : '正式微调模型' }} · 模型：{{ settings.model_name }}</p>
      <p><template v-if="settings.mode !== 'external_api'">发布产物：{{ settings.release_verified ? '已验证' : '未验证' }} · </template>检索校准：{{ settings.calibration_verified ? '已验证' : '未验证' }} · 密钥：{{ settings.has_api_key ? '已设置' : '未设置' }}</p>
      <p v-if="settings.mode === 'external_api'" class="field-hint">外部 API 联调未通过正式发布验证。{{ settings.calibration_verified ? '独立检索校准已验证。' : '检索校准未验证，当前未启用校准阈值。' }}</p>
      <p class="field-hint">检索配置标识：{{ settings.configuration_id }}</p>
    </template>
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="模型模式"><el-select v-model="form.mode" @change="changeMode"><el-option label="正式微调模型" value="finetuned" /><el-option label="外部 API 联调" value="external_api" /></el-select></el-form-item>
      <el-form-item label="模型服务地址"><el-input v-model="form.base_url" :placeholder="form.mode === 'external_api' ? 'DeepSeek：https://api.deepseek.com' : 'http://127.0.0.1:8000'" /></el-form-item>
      <el-form-item :label="form.mode === 'external_api' ? '外部 API 模型名称' : '发布模型别名'"><el-input v-model="form.model_name" :readonly="form.mode === 'finetuned'" placeholder="例如 deepseek-flash" /></el-form-item>
      <el-form-item label="服务密钥（留空保留原密钥）"><el-input v-model="form.api_key" type="password" autocomplete="new-password" /></el-form-item>
      <el-form-item v-if="form.mode === 'finetuned'" label="发布清单文件路径（服务端可访问）"><el-input v-model="form.release_manifest_path" placeholder="release-manifest.json 的绝对路径" /></el-form-item>
      <el-form-item :label="form.mode === 'external_api' ? '独立检索校准报告路径（可选）' : '人工审核的独立检索校准报告路径'"><el-input v-model="form.calibration_manifest_path" /></el-form-item>
      <p v-if="form.mode === 'external_api'" class="field-hint">外部 API 使用填写的模型名称，无需发布清单。未提供独立校准报告时明确标记未验证；提供后必须通过严格校验。</p>
      <p v-else class="field-hint">正式模型固定为 kb-grounded-qwen3-4b-v1。发布清单及校准报告须通过文件摘要和质量门槛检查；尚未交付真实微调产物时，生成问答明确失败。</p>
      <p class="field-hint">首次设置密钥前，服务端须提供独立 CONFIG_ENCRYPTION_KEY。密钥和路径不会读回，提交后立即清除页面内存中的新密钥。</p>
      <el-button native-type="submit" type="primary" :loading="busy">验证并保存</el-button>
    </el-form>
  </section>
</template>
