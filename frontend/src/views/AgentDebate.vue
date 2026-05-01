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
        <div class="analysis-status" :class="statusClass" aria-live="polite">
          <el-icon v-if="store.analysisStatus === 'running'" class="is-loading">
            <Loading />
          </el-icon>
          <span>{{ statusLabel }}</span>
        </div>
        <el-button
          type="primary"
          size="small"
          :loading="isStarting || isStreaming"
          :disabled="!selectedStock || isStarting || isStreaming"
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
            @input="onSearchInput"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
        <div class="history-list">
          <div
            v-if="store.historyLoading"
            class="history-placeholder"
            role="status"
            aria-live="polite"
          >
            正在加载历史记录…
          </div>
          <div
            v-else-if="store.historyError"
            class="history-placeholder history-placeholder--error"
            role="alert"
          >
            <span>{{ store.historyError }}</span>
            <el-button
              link
              type="primary"
              size="small"
              @click="store.fetchHistory()"
            >
              重试
            </el-button>
          </div>
          <div
            v-else-if="store.filteredHistory.length === 0"
            class="history-placeholder"
          >
            暂无历史记录
          </div>
          <div
            v-else
            class="history-options"
            role="listbox"
            aria-label="分析历史记录"
          >
            <div
              v-for="item in store.filteredHistory"
              :key="item.id"
              class="history-item"
              :class="{ active: item.id === currentId }"
              role="option"
              tabindex="0"
              :aria-selected="item.id === currentId ? 'true' : 'false'"
              @click="loadAnalysis(item.id)"
              @keydown.enter.prevent="loadAnalysis(item.id)"
              @keydown.space.prevent="loadAnalysis(item.id)"
            >
              <div class="history-item-main">
                <span class="history-stock">{{ item.stock_code }}</span>
                <span class="history-name">{{ item.stock_name }}</span>
              </div>
              <div class="history-item-sub">
                <span class="history-date">{{ item.trade_date }}</span>
                <el-tag
                  v-if="item.action"
                  :type="actionTagType(item.action)"
                  size="small"
                  effect="dark"
                >
                  {{ item.action }}
                </el-tag>
                <span class="history-score">{{ formatConfidence(item.confidence) }}</span>
              </div>
            </div>
          </div>
        </div>
      </aside>

      <!-- Main Content -->
      <main class="debate-main">
        <div
          class="debate-content"
          v-if="store.currentAnalysis"
          tabindex="-1"
          ref="streamingRegion"
          aria-label="分析结果"
        >
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

        <div
          v-else-if="store.analysisStatus === 'failed'"
          class="debate-empty"
          role="alert"
          tabindex="-1"
          ref="failedRegion"
        >
          <el-result
            icon="error"
            title="分析失败"
            :sub-title="store.lastError ?? '分析过程出现错误，请重试或查看日志'"
          >
            <template #extra>
              <el-button
                v-if="selectedStock"
                type="primary"
                :loading="isStarting || isStreaming"
                @click="startAnalysis"
              >
                重试分析
              </el-button>
              <el-button
                v-if="currentId && currentId !== 'provisional'"
                @click="retryLoadDetail"
              >
                重新加载记录
              </el-button>
            </template>
          </el-result>
        </div>
        <div v-else class="debate-empty">
          <el-empty description="选择标的或从历史记录加载分析结果" :image-size="120" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { Loading, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAgentStore } from '@/stores/agent'
import { useAgentSSE } from '@/composables/useSSE'
import { analysisApi } from '@/api/analysis'
import DebatePanel from '@/components/agent/DebatePanel.vue'
import DebateTimeline from '@/components/agent/DebateTimeline.vue'
import DecisionCard from '@/components/agent/DecisionCard.vue'

const store = useAgentStore()
const sseState = useAgentSSE()

const selectedStock = ref('')
const selectedDate = ref('')
const currentId = ref('')
const stockSearchLoading = ref(false)
const isStarting = ref(false)
// Track an active job explicitly rather than relying on the transient
// `sseState.connected.value`, which flaps false on any transport drop
// and would otherwise let the user kick off a second expensive job
// while the backend pipeline is still running.
const activeJobId = ref<string | null>(null)
const isStreaming = computed(() => activeJobId.value !== null)
const streamingRegion = ref<HTMLElement | null>(null)
const failedRegion = ref<HTMLElement | null>(null)

let historyFetchTimer: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  // Debounce keystrokes so each typed character does not hit the
  // backend. Date changes and blank queries fire immediately via
  // onDateChanged / clear-button handlers below.
  if (historyFetchTimer) clearTimeout(historyFetchTimer)
  historyFetchTimer = setTimeout(() => {
    store.fetchHistory()
  }, 250)
}

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
  if (!selectedStock.value || isStarting.value || isStreaming.value) return

  isStarting.value = true
  sseState.reset()
  const option = stockOptions.value.find((s) => s.value === selectedStock.value)
  const name = option?.label?.split(' ').slice(1).join(' ') || selectedStock.value
  // Seed a provisional AnalysisDetail so incoming SSE debate events
  // immediately populate the main panel instead of being dropped while
  // `currentAnalysis` is null.
  store.beginStreamingRun(selectedStock.value, name)

  try {
    const job = await analysisApi.createJob({
      stock_code: selectedStock.value,
      max_debate_rounds: 2,
    })
    currentId.value = job.job_id
    activeJobId.value = job.job_id
    sseState.connect(job.job_id)
    // Move focus to the streaming region so screen-reader and keyboard
    // users land on the newly-updating content instead of the now-
    // disabled Start button.
    await nextTick()
    streamingRegion.value?.focus()
  } catch (err: unknown) {
    store.analysisStatus = 'failed'
    const msg = err instanceof Error ? err.message : '分析启动失败'
    store.lastError = msg
    ElMessage.error(msg)
    activeJobId.value = null
    await nextTick()
    failedRegion.value?.focus()
  } finally {
    isStarting.value = false
  }
}

// Forward completed agent events into Pinia store as they arrive.
watch(sseState.events, (events) => {
  if (events.length === 0) return
  const latest = events[events.length - 1]
  store.applySSEEvent(latest)
})

// When the pipeline completes, load the full AnalysisRecord and refresh
// the history list so the new run appears at the top. Backend may emit
// a null `record_id` when persistence fails — in that case we mark the
// run as completed without overwriting the provisional detail so the
// user still sees the streamed debate.
watch(sseState.pipelineRecordId, async (recordId) => {
  if (!sseState.completed.value) return
  if (recordId) {
    await store.fetchDetail(recordId)
  } else if (store.analysisStatus === 'running') {
    store.analysisStatus = 'completed'
  }
  await store.fetchHistory()
  // Terminal success: release the activeJob guard so the Start button
  // becomes clickable again.
  activeJobId.value = null
})

// Surface SSE-side errors (e.g. upstream pipeline failure, disconnected
// EventSource) as user-facing toasts without silently losing state. If
// the backend included a `record_id` on the error event, load the
// failed record so the user can inspect which agent stopped the run.
watch(
  () => ({
    msg: sseState.error.value,
    recordId: sseState.errorRecordId.value,
  }),
  async ({ msg, recordId }) => {
    if (!msg) return
    ElMessage.error(msg)
    store.lastError = msg
    if (store.analysisStatus !== 'completed') {
      store.analysisStatus = 'failed'
    }
    if (recordId) {
      try {
        await store.fetchDetail(recordId)
      } catch {
        // fetchDetail already logs and sets state; nothing else to do.
      }
      await store.fetchHistory()
    }
    // Terminal failure: unlock the Start button and focus the retry UI.
    activeJobId.value = null
    await nextTick()
    failedRegion.value?.focus()
  },
)

async function loadAnalysis(id: string) {
  currentId.value = id
  sseState.reset()
  await store.fetchDetail(id)
}

async function retryLoadDetail() {
  if (!currentId.value || currentId.value === 'provisional') return
  await store.fetchDetail(currentId.value)
}

function onApprove() {
  ElMessage.success('交易指令已批准执行')
}

function onReject() {
  ElMessage.warning('交易指令已拒绝')
}

type ElTagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

function actionTagType(action: string | null): ElTagType {
  if (!action) return 'info'
  const map: Record<string, ElTagType> = {
    '买入': 'danger',
    '卖出': 'success',
    '持有': 'warning',
  }
  return map[action] ?? 'info'
}

function formatConfidence(confidence: number | null): string {
  if (confidence === null || confidence === undefined) return '--'
  return `${Math.round(confidence * 100)}分`
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

.history-placeholder {
  padding: 20px;
  text-align: center;
  color: $text-muted;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;

  &--error {
    color: $status-red;
  }
}

.history-item {
  outline: none;

  &:focus-visible {
    background: rgba(68, 138, 255, 0.08);
    box-shadow: inset 0 0 0 2px $color-accent;
  }
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
