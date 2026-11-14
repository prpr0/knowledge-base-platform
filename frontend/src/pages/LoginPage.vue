<!-- 通过真实API验证凭证并建立内存会话。 -->
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useSessionStore } from '../stores/session'
const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const busy = ref(false)
const error = ref('')
const rules: FormRules = {
  username: [{ required: true, whitespace: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
/** 使用真实账号登录，密码提交后立即从表单内存清除。 */
async function submit(): Promise<void> {
  if (busy.value || !(await formRef.value?.validate().catch(() => false))) return
  busy.value = true
  error.value = ''
  try {
    await session.login(form.username.trim(), form.password)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect.startsWith('/') && !redirect.startsWith('//') && redirect !== '/login' ? redirect : '/')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '登录失败，请重试' }
  finally { busy.value = false; form.password = '' }
}
</script>
<template>
  <main class="login-page">
    <section class="login-intro"><div class="brand light"><span class="brand-symbol">知</span><span>知识库<span class="brand-subtitle">KNOWLEDGE WORKSPACE</span></span></div><div><div class="eyebrow">让知识在合适的人之间流动</div><h1>清晰的组织，<br />可信的知识访问。</h1><p>统一维护部门、用户与角色，<br />为每一份知识设置准确的访问范围。</p></div><div class="login-footnote">组织与权限 · 工作空间</div></section>
    <section class="login-panel"><div class="login-form"><div class="eyebrow">欢迎回来</div><h2>登录控制台</h2><p class="muted">使用管理员为你创建的账号继续。</p><el-alert v-if="session.expired" title="登录状态已失效，请重新登录" type="warning" :closable="false" show-icon /><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="form-alert" /><el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large" @submit.prevent="submit"><el-form-item label="用户名" prop="username"><el-input v-model="form.username" autocomplete="username" placeholder="请输入用户名" :disabled="busy" /></el-form-item><el-form-item label="密码" prop="password"><el-input v-model="form.password" type="password" show-password autocomplete="current-password" placeholder="请输入密码" :disabled="busy" /></el-form-item><el-button type="primary" native-type="submit" :loading="busy" class="full-width login-submit">登录</el-button></el-form><p class="field-hint">登录凭证仅保留在当前页面内存，刷新或关闭页面后需重新登录。</p></div></section>
  </main>
</template>
