<template>
  <div class="debate-layout">
    <!-- Header Bar -->
    <div class="debate-header">
      <div class="header-left">
        <el-select
          v-model="selectedStock"
          filterable
          remote
          placeholder="输入股票代码或名称..."
          :remote-method="searchStocks"
          :loading="stockSearchLoading"
          class="stock-selector"
          @change="onStockSelected"
        >
          <el-option
            v-for="s in stockOptions"
            :key="s.value"
            :label="s.label"
            :value="s.value"
          />
        </el-select>

        <el-date-picker
          v-model="selectedDate"
          type="date"
          placeholder="选择日期"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          class="date-selector"
          @change="onDateChanged"
        />
      </div>

      <div class="header-right">
        <div class="analysis-status" :class="statusClass">
          <el-icon v-if="store.analysisStatus === 'running'" class="is-loading">
            <Loading />
          </el-icon>
          <span>{{ statusLabel }}</span>
        </div>
        <el-button
          type="primary"
          size="small"
          :loading="store.loading"
          :disabled="!selectedStock"
          @click="startAnalysis"
        >
          开始分析
        </el-button>
      </div>
    </div>

    <div class="debate-body">
      <!-- Sidebar: History -->
      <aside class="history-sidebar">
        <div class="sidebar-header">
          <span class="sidebar-title">分析历史</span>
        </div>
        <div class="sidebar-search">
          <el-input
            v-model="store.searchQuery"
            placeholder="搜索股票..."
            size="small"
            clearable
            @input="store.fetchHistory"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
        <div class="history-list">
          <div
            v-for="item in store.filteredHistory"
            :key="item.id"
            class="history-item"
            :class="{ active: item.id === currentId }"
            @click="loadAnalysis(item.id)"
          >
            <div class="history-item-main">
              <span class="history-stock">{{ item.stock_code }}</span>
              <span class="history-name">{{ item.stock_name }}</span>
            </div>
            <div class="history-item-sub">
              <span class="history-date">{{ item.trade_date }}</span>
              <el-tag
                :type="actionTagType(item.action)"
                size="small"
                effect="dark"
              >
                {{ item.action }}
              </el-tag>
              <span class="history-score">{{ item.score }}分</span>
            </div>
          </div>
          <div v-if="store.filteredHistory.length === 0" class="history-empty">
            暂无历史记录
          </div>
        </div>
      </aside>

      <!-- Main Content -->
      <main class="debate-main">
        <div class="debate-content" v-if="store.currentAnalysis">
          <!-- Stock Info Banner -->
          <div class="stock-banner">
            <span class="stock-code">{{ store.currentAnalysis.stock_code }}</span>
            <span class="stock-name">{{ store.currentAnalysis.stock_name }}</span>
            <span class="stock-date">{{ store.currentAnalysis.trade_date }}</span>
          </div>

          <!-- Two-Column Debate View -->
          <el-card shadow="never" class="section-card">
            <template #header>
              <span class="card-title">多空辩论</span>
            </template>
            <DebatePanel
              :rounds="store.debates"
              :thinking-agent="sseState.thinkingAgent.value"
            />
          </el-card>

          <!-- Debate Timeline -->
          <el-card shadow="never" class="section-card">
            <template #header>
              <span class="card-title">辩论时间线</span>
            </template>
            <DebateTimeline
              :rounds="store.debates"
              :current-round="store.currentRound"
              :max-rounds="store.maxRounds"
            />
          </el-card>

          <!-- Risk & Decision -->
          <DecisionCard
            :risk="store.riskAssessment"
            :decision="store.decision"
            :auth-mode="store.authMode"
            @auth-change="store.setAuthMode"
            @approve="onApprove"
            @reject="onReject"
          />
        </div>

        <div v-else class="debate-empty">
          <el-empty description="选择标的或从历史记录加载分析结果" :image-size="120" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { Loading, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import { useAgentSSE } from '@/composables/useSSE'
import DebatePanel from '@/components/agent/DebatePanel.vue'
import DebateTimeline from '@/components/agent/DebateTimeline.vue'
import DecisionCard from '@/components/agent/DecisionCard.vue'

const store = useAgentStore()
const sseState = useAgentSSE()

const selectedStock = ref('')
const selectedDate = ref('')
const currentId = ref('')
const stockSearchLoading = ref(false)

interface StockOption {
  readonly value: string
  readonly label: string
}

const stockOptions = ref<StockOption[]>([
  { value: '600519', label: '600519 贵州茅台' },
  { value: '000858', label: '000858 五粮液' },
  { value: '300750', label: '300750 宁德时代' },
  { value: '601318', label: '601318 中国平安' },
  { value: '000001', label: '000001 平安银行' },
  { value: '600036', label: '600036 招商银行' },
  { value: '002594', label: '002594 比亚迪' },
  { value: '601899', label: '601899 紫金矿业' },
])

const statusClass = computed(() => `status-${store.analysisStatus}`)

const statusLabel = computed(() => {
  const labels: Record<string, string> = {
    pending: '等待分析',
    running: '分析中...',
    completed: '已完成',
    failed: '分析失败',
  }
  return labels[store.analysisStatus] ?? ''
})

function searchStocks(query: string) {
  if (!query) return
  stockSearchLoading.value = true
  // In dev mode, filter from static list; in production, call backend
  setTimeout(() => {
    stockSearchLoading.value = false
  }, 200)
}

function onStockSelected() {
  // Stock selected, ready for analysis
}

function onDateChanged() {
  store.searchDate = selectedDate.value
  store.fetchHistory()
}

async function startAnalysis() {
  if (!selectedStock.value) return

  sseState.reset()

  // Trigger analysis — backend currently runs synchronously and returns when done.
  // Once SSE endpoint is implemented, connect the stream before triggering so we
  // receive round-by-round events as they happen.
  const id = await store.triggerAnalysis(selectedStock.value)
  if (id) {
    currentId.value = id
    // Connect SSE for future real-time streaming support
    sseState.connect(id)
    await store.fetchDetail(id)
  } else if (import.meta.env.DEV) {
    // Dev-only fallback: load mock data when backend is unavailable
    currentId.value = 'mock-001'
    await store.fetchDetail('mock-001')
  }
}

// Forward SSE events into Pinia store as they arrive
watch(sseState.events, (events) => {
  if (events.length === 0) return
  const latest = events[events.length - 1]
  store.applySSEEvent(latest)
})

async function loadAnalysis(id: string) {
  currentId.value = id
  sseState.reset()
  await store.fetchDetail(id)
}

function onApprove() {
  ElMessage.success('交易指令已批准执行')
}

function onReject() {
  ElMessage.warning('交易指令已拒绝')
}

type ElTagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

function actionTagType(action: string): ElTagType {
  const map: Record<string, ElTagType> = {
    '买入': 'danger',
    '卖出': 'success',
    '持有': 'warning',
  }
  return map[action] ?? 'info'
}

onMounted(() => {
  store.fetchHistory()
})
</script>

<style lang="scss" scoped>
.debate-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: $bg-primary;
}

// Header
.debate-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px $gap-md;
  background: $bg-header;
  border-bottom: 1px solid $border-color;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.stock-selector {
  width: 260px;
}

.date-selector {
  width: 160px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.analysis-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  padding: 4px 12px;
  border-radius: 4px;

  &.status-pending { color: $text-muted; }
  &.status-running { color: $color-accent; background: rgba(68, 138, 255, 0.1); }
  &.status-completed { color: $status-green; background: rgba(0, 200, 83, 0.1); }
  &.status-failed { color: $status-red; background: rgba(255, 23, 68, 0.1); }
}

// Body: sidebar + main
.debate-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

// Sidebar
.history-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: $bg-sidebar;
  border-right: 1px solid $border-color;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-header {
  padding: 12px 16px;
  border-bottom: 1px solid $border-color;
}

.sidebar-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.sidebar-search {
  padding: 8px 12px;
}

.history-list {
  flex: 1;
  overflow-y: auto;
}

.history-item {
  padding: 10px 16px;
  cursor: pointer;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  transition: background 0.15s;

  &:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  &.active {
    background: rgba(68, 138, 255, 0.1);
    border-left: 3px solid $color-accent;
  }
}

.history-item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.history-stock {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}

.history-name {
  font-size: 12px;
  color: $text-secondary;
}

.history-item-sub {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-date {
  font-size: 11px;
  color: $text-muted;
}

.history-score {
  font-size: 11px;
  color: $text-muted;
  margin-left: auto;
}

.history-empty {
  padding: 20px;
  text-align: center;
  color: $text-muted;
  font-size: 12px;
}

// Main content area
.debate-main {
  flex: 1;
  overflow-y: auto;
  padding: $gap-md;
}

.debate-content {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
  max-width: 1200px;
}

.stock-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}

.stock-code {
  font-size: 20px;
  font-weight: 700;
  color: $text-primary;
}

.stock-name {
  font-size: 16px;
  color: $text-secondary;
}

.stock-date {
  font-size: 13px;
  color: $text-muted;
  margin-left: auto;
}

.section-card {
  .card-title {
    font-size: 14px;
    font-weight: 600;
    color: $text-primary;
  }
}

.debate-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 60vh;
}
</style>
