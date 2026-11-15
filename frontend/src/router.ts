/** 按当前服务端身份保护页面导航。 */
import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useSessionStore } from './stores/session'

/** 路由只控制界面入口，API 始终独立执行服务端授权。 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./pages/LoginPage.vue'), meta: { title: '登录' } },
    { path: '/', component: () => import('./pages/OverviewPage.vue'), meta: { title: '工作台' } },
    { path: '/departments', component: () => import('./pages/DepartmentsPage.vue'), meta: { title: '部门管理', permissions: ['organization:read', 'department:manage'] } },
    { path: '/users', component: () => import('./pages/UsersPage.vue'), meta: { title: '用户管理', permissions: ['organization:read', 'user:manage'] } },
    { path: '/roles', component: () => import('./pages/RolesPage.vue'), meta: { title: '角色权限', permissions: ['organization:read', 'role:manage'] } },
    { path: '/knowledge', component: () => import('./pages/KnowledgePage.vue'), meta: { title: '知识与授权', permissions: ['knowledge:read', 'knowledge:manage'] } },
    { path: '/imports', component: () => import('./pages/ImportsPage.vue'), meta: { title: '知识导入中心', permissions: ['knowledge:manage'] } },
    { path: '/search', component: () => import('./pages/SearchPage.vue'), meta: { title: '知识检索', permissions: ['knowledge:read'] } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

/** 进入页面前重新获取当前身份，撤权后及时移除入口。 */
router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (to.name === 'login') return session.authenticated ? '/' : true
  if (!session.authenticated) return { name: 'login', query: { redirect: to.fullPath } }
  try { await session.refresh() } catch (error) {
    if (!session.authenticated) return { name: 'login', query: { redirect: to.fullPath } }
    ElMessage.error(error instanceof Error ? error.message : '刷新身份失败，请重试')
    return false
  }
  if (!session.authenticated) return { name: 'login' }
  const required = to.meta.permissions as string[] | undefined
  if (required && !session.canAny(required)) {
    ElMessage.error('权限不足，当前账号无法进入该页面')
    return '/'
  }
  document.title = String(to.meta.title) + ' · 知识库控制台'
  return true
})
export default router
