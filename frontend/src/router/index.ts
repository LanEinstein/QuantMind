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
    meta: { title: '大盘监控' },
  },
  {
    path: '/system-status',
    name: 'SystemStatus',
    component: () => import('@/views/SystemStatus.vue'),
    meta: { title: '系统状态' },
  },
  {
    path: '/instruction-plans',
    name: 'InstructionPlans',
    component: () => import('@/views/InstructionPlans.vue'),
    meta: { title: 'InstructionPlan 池' },
  },
  {
    path: '/portfolio',
    name: 'Portfolio',
    component: () => import('@/views/Portfolio.vue'),
    meta: { title: '组合管理' },
  },
  {
    path: '/performance',
    name: 'Performance',
    component: () => import('@/views/Performance.vue'),
    meta: { title: '绩效报告' },
  },
  {
    path: '/acceptance-reports',
    name: 'AcceptanceReports',
    component: () => import('@/views/AcceptanceReports.vue'),
    meta: { title: '验收报告' },
  },
  {
    path: '/risk-center',
    name: 'RiskCenter',
    component: () => import('@/views/RiskCenter.vue'),
    meta: { title: '风控中心' },
  },
  // P1-5 §2 redline: Simulation.vue + AgentDebate.vue stay in code but
  // are intentionally absent from AppShell menu (G-008 Phase B 收尾 deferred
  // for AgentDebate; Simulation visualization scope deferred to later phase).
  {
    path: '/agent-debate',
    name: 'AgentDebate',
    component: () => import('@/views/AgentDebate.vue'),
    meta: { title: 'Agent辩论(只读历史)', hidden: true },
  },
  {
    path: '/simulation',
    name: 'Simulation',
    component: () => import('@/views/Simulation.vue'),
    meta: { title: 'MiroFish仿真', hidden: true },
  },
  {
    path: '/settings',
    component: () => import('@/views/settings/SettingsLayout.vue'),
    meta: { title: '系统设置' },
    redirect: '/settings/llm-router',
    children: [
      {
        path: 'llm-router',
        name: 'SettingsLLMRouter',
        component: () => import('@/views/settings/LLMRouter.vue'),
        meta: { title: 'LLM路由配置' },
      },
      {
        path: 'data-sources',
        name: 'SettingsDataSources',
        component: () => import('@/views/settings/DataSources.vue'),
        meta: { title: '数据源' },
      },
      {
        path: 'mirofish',
        name: 'SettingsMiroFish',
        component: () => import('@/views/settings/MiroFishConfig.vue'),
        meta: { title: 'MiroFish配置' },
      },
      {
        path: 'cost-dashboard',
        name: 'SettingsCostDashboard',
        component: () => import('@/views/settings/CostDashboard.vue'),
        meta: { title: '成本统计' },
      },
    ],
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
