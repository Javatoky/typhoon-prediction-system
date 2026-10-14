import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: [
      {
        path: '',
        name: 'workspace',
        component: () => import('@/views/WorkspaceView.vue'),
        meta: { title: '预测工作台' },
      },
      {
        path: 'typhoons',
        name: 'typhoons',
        component: () => import('@/views/TyphoonHistoryView.vue'),
        meta: { title: '历史台风' },
      },
      {
        path: 'tasks',
        name: 'tasks',
        component: () => import('@/views/TaskListView.vue'),
        meta: { title: '我的任务' },
      },
      {
        path: 'tasks/:id',
        name: 'task-detail',
        component: () => import('@/views/TaskDetailView.vue'),
        meta: { title: '任务详情' },
      },
      {
        path: 'profile',
        name: 'profile',
        component: () => import('@/views/ProfileView.vue'),
        meta: { title: '账号设置' },
      },
      {
        path: 'admin',
        component: () => import('@/views/admin/AdminLayout.vue'),
        meta: { requiresAdmin: true },
        children: [
          { path: '', redirect: { name: 'admin-overview' } },
          {
            path: 'overview',
            name: 'admin-overview',
            component: () => import('@/views/admin/AdminOverviewView.vue'),
            meta: { requiresAdmin: true, title: '管理概览' },
          },
          {
            path: 'users',
            name: 'admin-users',
            component: () => import('@/views/admin/UserManageView.vue'),
            meta: { requiresAdmin: true, title: '用户管理' },
          },
          {
            path: 'datasets',
            name: 'admin-datasets',
            component: () => import('@/views/admin/DatasetManageView.vue'),
            meta: { requiresAdmin: true, title: '数据集管理' },
          },
          {
            path: 'models',
            name: 'admin-models',
            component: () => import('@/views/admin/ModelManageView.vue'),
            meta: { requiresAdmin: true, title: '模型管理' },
          },
          {
            path: 'logs',
            name: 'admin-logs',
            component: () => import('@/views/admin/AuditLogView.vue'),
            meta: { requiresAdmin: true, title: '操作日志' },
          },
        ],
      },
      { path: ':pathMatch(.*)*', redirect: { name: 'workspace' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

/**
 * 路由守卫：只做体验优化（未登录跳转登录页、非管理员隐藏管理入口）。
 * 权限的最终判定仍在服务端（7.1：无权限接口仍须拒绝）。
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.ensureReady()

  if (to.meta.public) {
    return auth.isLoggedIn && to.name === 'login' ? { name: 'workspace' } : true
  }
  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return { name: 'workspace' }
  }
  return true
})

router.afterEach((to) => {
  const title = to.meta.title ? `${to.meta.title} - ` : ''
  document.title = `${title}台风路径智能预报系统`
})

export default router
