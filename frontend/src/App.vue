<!-- 知识库控制台布局、权限导航及退出会话。 -->
<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from './stores/session'

const session = useSessionStore()
const route = useRoute()
const router = useRouter()
const menu = computed(() => [
  { path: '/', title: '工作台', mark: '01', show: true },
  { path: '/departments', title: '部门管理', mark: '02', show: session.canAny(['organization:read', 'department:manage']) },
  { path: '/users', title: '用户管理', mark: '03', show: session.canAny(['organization:read', 'user:manage']) },
  { path: '/roles', title: '角色权限', mark: '04', show: session.canAny(['organization:read', 'role:manage']) },
  { path: '/knowledge', title: '知识与授权', mark: '05', show: session.canAny(['knowledge:read', 'knowledge:manage']) },
  { path: '/imports', title: '知识导入中心', mark: '06', show: session.can('knowledge:manage') },
  { path: '/search', title: '知识检索', mark: '07', show: session.can('knowledge:read') },
  { path: '/chat', title: 'AI智能问答', mark: '08', show: session.can('chat:ask') },
  { path: '/faq', title: 'FAQ挖掘审核', mark: '09', show: session.can('faq:manage') },
  { path: '/gaps', title: '知识缺口', mark: '10', show: session.can('gap:manage') },
  { path: '/metrics', title: '运营看板', mark: '11', show: session.can('metrics:read') },
  { path: '/audits', title: '安全审计', mark: '12', show: session.can('audit:read') },
  { path: '/settings/model', title: '模型设置', mark: '13', show: session.can('model:manage') },
].filter((item) => item.show))

/** 失效或主动退出均返回登录页，不保留业务组件内的正文。 */
watch(() => session.authenticated, (active) => { if (!active && route.name !== 'login') void router.replace('/login') })
/** 当前菜单权限在操作后被撤回时，及时退出受保护页面。 */
watch(() => session.user?.permissions, () => {
  const permissions = route.meta.permissions as string[] | undefined
  if (session.authenticated && permissions && !session.canAny(permissions)) void router.replace('/')
})
/** 清除页面内存身份并返回登录。 */
function logout(): void { session.clear(); void router.replace('/login') }
</script>

<template>
  <RouterView v-if="!session.authenticated" />
  <div v-else class="workspace">
    <aside class="sidebar">
      <RouterLink to="/" class="brand"><span class="brand-symbol">知</span><span>知识库<span class="brand-subtitle">知识与权限控制台</span></span></RouterLink>
      <div class="sidebar-caption">工作空间</div>
      <nav class="navigation" aria-label="主导航">
        <RouterLink v-for="item in menu" :key="item.path" :to="item.path" :class="{ selected: route.path === item.path }"><span class="nav-mark">{{ item.mark }}</span>{{ item.title }}</RouterLink>
      </nav>
      <div class="sidebar-note"><span class="status-dot"></span> 权限以服务端当前状态为准</div>
    </aside>
    <section class="main-column">
      <header class="topbar"><div class="breadcrumb">控制台 <span>/</span> <strong>{{ route.meta.title }}</strong></div><div class="account"><span class="avatar">{{ (session.user?.display_name || session.user?.username || '用').slice(0, 1) }}</span><div><strong>{{ session.user?.display_name || session.user?.username }}</strong><small>{{ session.user?.username }}</small></div><el-button text @click="logout">退出登录</el-button></div></header>
      <main class="main-content"><RouterView /></main>
      <footer class="footer">知识库管理平台 · 文档与权限控制台</footer>
    </section>
  </div>
</template>
