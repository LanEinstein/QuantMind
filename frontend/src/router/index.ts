import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '大盘监控', icon: 'Monitor' },
  },
  {
    path: '/agent-debate',
    name: 'AgentDebate',
    component: () => import('@/views/AgentDebate.vue'),
    meta: { title: 'Agent辩论', icon: 'ChatDotRound' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  document.title = `${(to.meta.title as string) || 'QuantMind'} - QuantMind`
})

export default router
