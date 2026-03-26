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
  {
    path: '/simulation',
    name: 'Simulation',
    component: () => import('@/views/Simulation.vue'),
    meta: { title: 'MiroFish仿真', icon: 'TrendCharts' },
  },
  {
    path: '/portfolio',
    name: 'Portfolio',
    component: () => import('@/views/Portfolio.vue'),
    meta: { title: '组合管理', icon: 'Briefcase' },
  },
  {
    path: '/performance',
    name: 'Performance',
    component: () => import('@/views/Performance.vue'),
    meta: { title: '绩效报告', icon: 'DataAnalysis' },
  },
  {
    path: '/risk-center',
    name: 'RiskCenter',
    component: () => import('@/views/RiskCenter.vue'),
    meta: { title: '风控中心', icon: 'Shield' },
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
