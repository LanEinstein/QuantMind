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
  // Post-MI-1 (2026-08-24): the line-split mirror ledger panel, backed by
  // the standalone read-only API (scripts/account_api.py) — not the sealed
  // dual-line runtime that /portfolio still talks to.
  {
    path: '/account-lines',
    name: 'AccountLines',
    component: () => import('@/views/AccountLines.vue'),
    meta: { title: '分线账本' },
  },
  {
    path: '/execution-reports',
    name: 'ExecutionReports',
    component: () => import('@/views/ExecutionReportEntry.vue'),
    meta: { title: '用户回报录入' },
  },
  {
    path: '/reconciliation-center',
    name: 'ReconciliationCenter',
    component: () => import('@/views/ReconciliationCenter.vue'),
    meta: { title: '对账裁定中心' },
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
  // G-008 — Phase B 收尾 reveals AgentDebate in the main menu (P1-5 §1.1
  // 4 Phase B-finale pages).
  {
    path: '/agent-debate',
    name: 'AgentDebate',
    component: () => import('@/views/AgentDebate.vue'),
    meta: { title: 'Agent 辩论(只读历史)' },
  },
  {
    path: '/data-quality',
    name: 'DataQuality',
    component: () => import('@/views/DataQuality.vue'),
    meta: { title: '数据质量' },
  },
  {
    path: '/feishu-messages',
    name: 'FeishuMessages',
    component: () => import('@/views/FeishuMessages.vue'),
    meta: { title: '飞书消息历史' },
  },
  {
    path: '/cost-breakdown',
    name: 'CostBreakdown',
    component: () => import('@/views/CostBreakdown.vue'),
    meta: { title: '成本拆解面板' },
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
