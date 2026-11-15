<!-- 展示当前身份与获准工作入口。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useSessionStore } from '../stores/session'
import { usePageState } from '../composables/usePageState'
import PageHeader from '../components/PageHeader.vue'
const session = useSessionStore()
const updated = ref('')
const { loading, error, reload } = usePageState(async () => { updated.value = new Date().toLocaleTimeString('zh-CN') })
const links = computed(() => [
  { title: '部门管理', description: '维护部门层级与直属关系', path: '/departments', allowed: session.canAny(['organization:read', 'department:manage']) },
  { title: '用户管理', description: '分配部门、角色与账号状态', path: '/users', allowed: session.canAny(['organization:read', 'user:manage']) },
  { title: '角色权限', description: '为角色配置菜单与操作权限', path: '/roles', allowed: session.canAny(['organization:read', 'role:manage']) },
  { title: '知识与授权', description: '维护知识内容与四维访问范围', path: '/knowledge', allowed: session.canAny(['knowledge:read', 'knowledge:manage']) },
  { title: '知识导入中心', description: '导入文件或文件夹，查看任务和失败重试', path: '/imports', allowed: session.can('knowledge:manage') },
  { title: '知识检索', description: '检索当前身份获准读取的来源与片段', path: '/search', allowed: session.can('knowledge:read') },
].filter((item) => item.allowed))
onMounted(reload)
</script>
<template>
  <PageHeader title="工作台" description="查看当前身份与可用工作入口。"><el-button :loading="loading" @click="reload">刷新身份</el-button></PageHeader>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
  <section class="welcome-panel"><div><div class="eyebrow">当前工作身份</div><h2>你好，{{ session.user?.display_name || session.user?.username }}</h2><p>从组织关系到知识授权，在同一个工作空间管理。</p></div><span class="welcome-emblem">知</span></section>
  <div class="overview-grid"><section class="surface"><h3>账号信息</h3><dl class="identity-grid"><dt>用户名</dt><dd>{{ session.user?.username }}</dd><dt>用户 ID</dt><dd>{{ session.user?.id }}</dd><dt>直属部门</dt><dd>{{ session.user?.department_id === null ? '未分配' : '#' + session.user?.department_id }}</dd><dt>角色 ID</dt><dd>{{ session.user?.role_ids.length ? session.user.role_ids.map((id) => '#' + id).join('、') : '未分配' }}</dd><dt>权限版本</dt><dd>{{ session.user?.permission_version }}</dd><dt>身份刷新</dt><dd>{{ updated || '正在读取' }}</dd></dl></section><section class="surface"><h3>当前授权</h3><div v-if="session.user?.permissions.length" class="tag-list"><el-tag v-for="permission in session.user.permissions" :key="permission" effect="plain">{{ permission }}</el-tag></div><el-empty v-else description="当前账号未分配操作权限，请联系管理员" :image-size="70" /><p class="field-hint">角色、部门及权限变更后，进入页面或完成操作时会重新获取当前身份。</p></section></div>
  <section class="surface"><h3>工作入口</h3><div v-if="links.length" class="entry-grid"><RouterLink v-for="link in links" :key="link.path" :to="link.path" class="entry-card"><h4>{{ link.title }} <span>↗</span></h4><p>{{ link.description }}</p></RouterLink></div><el-empty v-else description="暂无可用入口，请联系管理员分配权限" :image-size="70" /></section>
</template>
