<template>
  <div class="debate-layout">
    <!-- Header Bar -->
    <div class="debate-header">
      <div class="header-left">
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
          <span>{{ statusLabel }}</span>
        </div>
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
            <DebatePanel :rounds="store.debates" :thinking-agent="null" />
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

          <!-- Risk & Decision (read-only history view) -->
          <DecisionCard
            :risk="store.riskAssessment"
            :decision="store.decision"
          />
        </div>

        <div v-else class="debate-empty">
          <el-empty description="从历史记录加载分析结果" :image-size="120" />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { useAgentStore } from '@/stores/agent'
import DebatePanel from '@/components/agent/DebatePanel.vue'
import DebateTimeline from '@/components/agent/DebateTimeline.vue'
import DecisionCard from '@/components/agent/DecisionCard.vue'

const store = useAgentStore()

const selectedDate = ref('')
const currentId = ref('')

let historyFetchTimer: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  if (historyFetchTimer) clearTimeout(historyFetchTimer)
  historyFetchTimer = setTimeout(() => {
    store.fetchHistory()
  }, 250)
}

const statusClass = computed(() => `status-${store.analysisStatus}`)

const statusLabel = computed(() => {
  const labels: Record<string, string> = {
    pending: '等待加载',
    running: '加载中...',
    completed: '已完成',
    failed: '加载失败',
  }
  return labels[store.analysisStatus] ?? ''
})

function onDateChanged() {
  store.searchDate = selectedDate.value
  store.fetchHistory()
}

async function loadAnalysis(id: string) {
  currentId.value = id
  await store.fetchDetail(id)
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

.debate-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

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
