<template>
  <div class="simulation-layout">
    <!-- Header -->
    <div class="simulation-header">
      <div class="header-left">
        <h2 class="event-title">{{ store.eventTitle || '事态推演' }}</h2>
        <span
          v-if="store.importanceScore > 0"
          class="importance-badge"
          :class="importanceClass"
        >
          {{ store.importanceScore }}/10
        </span>
      </div>
      <div class="header-right">
        <template v-if="store.simulationConfig">
          <span class="meta-item">
            <el-icon><User /></el-icon>
            {{ store.simulationConfig.agent_count }} Agents
          </span>
          <span class="meta-item">
            <el-icon><Refresh /></el-icon>
            {{ store.simulationConfig.rounds }} Rounds
          </span>
          <span class="meta-item">
            <el-icon><Timer /></el-icon>
            {{ formatDuration(currentSimulation?.duration_seconds ?? 0) }}
          </span>
          <span class="meta-item cost">
            ¥{{ (currentSimulation?.cost_rmb ?? 0).toFixed(2) }}
          </span>
        </template>
        <span v-if="currentSimulation" class="meta-timestamp">
          {{ formatTimestamp(currentSimulation.created_at) }}
        </span>
      </div>
    </div>

    <!-- Body -->
    <div class="simulation-body">
      <!-- History Sidebar -->
      <div class="history-sidebar">
        <div class="sidebar-header">仿真历史</div>
        <div class="sidebar-search">
          <el-input
            v-model="store.searchQuery"
            placeholder="搜索事件..."
            :prefix-icon="Search"
            clearable
            size="small"
            @input="onSearchInput"
          />
        </div>
        <div class="history-list">
          <div
            v-for="item in store.filteredHistory"
            :key="item.id"
            class="history-item"
            :class="{ active: item.id === currentId }"
            @click="loadSimulation(item.id)"
          >
            <div class="history-item-header">
              <span
                class="importance-dot"
                :class="importanceClassFor(item.importance_score)"
              />
              <span class="history-title">{{ item.event_title }}</span>
            </div>
            <div class="history-meta">
              <span>{{ item.agent_count }}A × {{ item.rounds }}R</span>
              <span>¥{{ item.cost_rmb.toFixed(2) }}</span>
              <span>{{ formatTimestamp(item.created_at) }}</span>
            </div>
          </div>
          <div v-if="store.filteredHistory.length === 0" class="history-empty">
            <el-empty description="暂无仿真记录" :image-size="80" />
          </div>
        </div>
      </div>

      <!-- Main Content -->
      <div class="simulation-main">
        <template v-if="currentSimulation">
          <!-- Zone Top: Sentiment Chart + Hidden Variables -->
          <el-row :gutter="12" class="zone-top">
            <el-col :span="14">
              <el-card shadow="never" class="chart-card zone-a-card">
                <template #header>
                  <span class="card-title">群体情绪演变</span>
                </template>
                <SentimentChart
                  :sentiment-data="store.sentimentData"
                  :inflection-points="store.inflectionPoints"
                />
              </el-card>
            </el-col>
            <el-col :span="10">
              <el-card shadow="never" class="chart-card zone-b-card">
                <template #header>
                  <span class="card-title">隐性变量矩阵</span>
                </template>
                <HiddenVariableMatrix :variables="store.hiddenVariables" />
              </el-card>
            </el-col>
          </el-row>

          <!-- Zone Bottom: Inflection Timeline + Extreme Scenario -->
          <el-row :gutter="12" class="zone-bottom">
            <el-col :span="10">
              <el-card shadow="never" class="chart-card zone-c-card">
                <template #header>
                  <span class="card-title">关键拐点时间线</span>
                </template>
                <InflectionTimeline
                  :inflection-points="store.inflectionPoints"
                  :sentiment-data="store.sentimentData"
                />
              </el-card>
            </el-col>
            <el-col :span="14">
              <el-card shadow="never" class="chart-card zone-d-card">
                <template #header>
                  <span class="card-title">极端场景分布</span>
                </template>
                <ExtremeScenarioPie :scenarios="store.extremeScenarios" />
              </el-card>
            </el-col>
          </el-row>

          <!-- Recommendation -->
          <div class="recommendation-bar">
            <el-icon><Promotion /></el-icon>
            <span class="recommendation-label">仿真结论:</span>
            <span class="recommendation-text">
              {{ currentSimulation.recommended_action }}
            </span>
          </div>

          <!-- Action Buttons -->
          <div class="bottom-actions">
            <el-button @click="showReport = true">
              <el-icon><Document /></el-icon>
              查看完整报告
            </el-button>
            <el-button @click="exportPdf">
              <el-icon><Download /></el-icon>
              导出PDF
            </el-button>
            <el-button @click="showCompare = true">
              <el-icon><Switch /></el-icon>
              与上次对比
            </el-button>
            <el-button type="primary" @click="navigateToDebate">
              <el-icon><ChatDotRound /></el-icon>
              注入Agent辩论
            </el-button>
          </div>
        </template>

        <div v-else-if="store.status === 'loading'" class="simulation-loading">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <span>加载仿真数据...</span>
        </div>

        <div v-else class="simulation-empty">
          <el-empty description="暂无仿真数据" :image-size="120" />
        </div>
      </div>
    </div>

    <!-- Full Report Dialog -->
    <el-dialog
      v-model="showReport"
      title="MiroFish 仿真完整报告"
      width="70%"
      :close-on-click-modal="true"
    >
      <template v-if="currentSimulation">
        <div class="report-content">
          <h3>{{ store.eventTitle }}</h3>
          <p class="report-meta">
            {{ store.simulationConfig?.agent_count }} Agents ×
            {{ store.simulationConfig?.rounds }} Rounds |
            耗时 {{ formatDuration(currentSimulation.duration_seconds) }} |
            成本 ¥{{ currentSimulation.cost_rmb.toFixed(2) }}
          </p>

          <h4>事件摘要</h4>
          <p>{{ currentSimulation.event_summary }}</p>

          <h4>隐性变量</h4>
          <div
            v-for="(hv, idx) in store.hiddenVariables"
            :key="idx"
            class="report-variable"
          >
            <strong>{{ hv.variable }}</strong>
            ({{ Math.round(hv.probability * 100) }}%):
            {{ hv.reasoning }}
          </div>

          <h4>关键拐点</h4>
          <ul>
            <li
              v-for="(ip, idx) in store.inflectionPoints"
              :key="idx"
            >
              Day {{ ip.day }}: {{ ip.event }}
            </li>
          </ul>

          <h4>极端场景</h4>
          <div
            v-for="(es, idx) in store.extremeScenarios"
            :key="idx"
            class="report-scenario"
          >
            <strong>{{ es.scenario }}</strong>
            ({{ Math.round(es.probability * 100) }}%):
            {{ es.impact }}
          </div>

          <h4>综合建议</h4>
          <p class="report-action">{{ currentSimulation.recommended_action }}</p>
        </div>
      </template>
    </el-dialog>

    <!-- Comparison Drawer -->
    <el-drawer
      v-model="showCompare"
      title="仿真对比"
      size="60%"
      direction="rtl"
    >
      <div class="compare-placeholder">
        <el-empty description="选择另一次仿真进行对比" :image-size="100" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Search,
  User,
  Refresh,
  Timer,
  Promotion,
  Document,
  Download,
  Switch,
  ChatDotRound,
  Loading,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { useSimulationStore } from '@/stores/simulation'
import SentimentChart from '@/components/charts/SentimentChart.vue'
import HiddenVariableMatrix from '@/components/charts/HiddenVariableMatrix.vue'
import InflectionTimeline from '@/components/charts/InflectionTimeline.vue'
import ExtremeScenarioPie from '@/components/charts/ExtremeScenarioPie.vue'

const router = useRouter()
const store = useSimulationStore()

const currentId = ref('')
const showReport = ref(false)
const showCompare = ref(false)

let searchDebounce: ReturnType<typeof setTimeout> | null = null

const currentSimulation = computed(() => store.currentSimulation)

const importanceClass = computed(() => {
  const score = store.importanceScore
  if (score >= 7) return 'importance-high'
  if (score >= 4) return 'importance-mid'
  return 'importance-low'
})

function importanceClassFor(score: number): string {
  if (score >= 7) return 'importance-high'
  if (score >= 4) return 'importance-mid'
  return 'importance-low'
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m${s}s`
}

function formatTimestamp(ts: string): string {
  return dayjs(ts).format('MM-DD HH:mm')
}

async function loadSimulation(id: string) {
  currentId.value = id
  await store.fetchById(id)
}

function navigateToDebate() {
  router.push('/agent-debate')
}

function exportPdf() {
  ElMessage.info('PDF导出功能开发中...')
}

function onSearchInput() {
  if (searchDebounce !== null) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    store.fetchHistory()
  }, 300)
}

onMounted(async () => {
  await Promise.allSettled([store.fetchLatest(), store.fetchHistory()])
  if (store.currentSimulation) {
    currentId.value = store.currentSimulation.id
  }
})
</script>

<style scoped lang="scss">
.simulation-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

// --- Header ---
.simulation-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px $gap-md;
  background: $bg-header;
  border-bottom: 1px solid $border-color;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: $gap-sm;
}

.event-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: $text-primary;
}

.importance-badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
  font-family: monospace;

  &.importance-high {
    background: $color-importance-high;
    color: $status-red;
  }

  &.importance-mid {
    background: $color-importance-mid;
    color: $status-yellow;
  }

  &.importance-low {
    background: $color-importance-low;
    color: $text-muted;
  }
}

.header-right {
  display: flex;
  align-items: center;
  gap: $gap-md;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: $text-secondary;

  .el-icon {
    font-size: 14px;
    color: $text-muted;
  }

  &.cost {
    color: $color-accent;
    font-weight: 600;
  }
}

.meta-timestamp {
  font-size: 11px;
  color: $text-muted;
  font-family: monospace;
}

// --- Body ---
.simulation-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

// --- Sidebar ---
.history-sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: $bg-sidebar;
  border-right: 1px solid $border-color;
}

.sidebar-header {
  padding: 12px $gap-md;
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
  border-bottom: 1px solid $border-color;
}

.sidebar-search {
  padding: $gap-sm $gap-md;
  border-bottom: 1px solid $border-color;
}

.history-list {
  flex: 1;
  overflow-y: auto;
}

.history-item {
  padding: 10px $gap-md;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  cursor: pointer;
  transition: background 0.15s;

  &:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  &.active {
    background: rgba(68, 138, 255, 0.08);
    border-left: 3px solid $color-accent;
    padding-left: 13px;
  }
}

.history-item-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.importance-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;

  &.importance-high {
    background: $status-red;
  }

  &.importance-mid {
    background: $status-yellow;
  }

  &.importance-low {
    background: $text-muted;
  }
}

.history-title {
  font-size: 13px;
  color: $text-primary;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-meta {
  display: flex;
  gap: $gap-sm;
  font-size: 11px;
  color: $text-muted;
}

.history-empty {
  padding: 32px $gap-md;
}

// --- Main Content ---
.simulation-main {
  flex: 1;
  overflow-y: auto;
  padding: $gap-md;
}

.zone-top,
.zone-bottom {
  margin-bottom: $gap-md;
}

.chart-card {
  :deep(.el-card__header) {
    padding: 10px 16px;
  }

  :deep(.el-card__body) {
    padding: 8px 12px;
  }
}

.zone-a-card :deep(.el-card__body),
.zone-b-card :deep(.el-card__body) {
  height: 340px;
}

.zone-c-card :deep(.el-card__body),
.zone-d-card :deep(.el-card__body) {
  height: 300px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

// --- Recommendation ---
.recommendation-bar {
  display: flex;
  align-items: flex-start;
  gap: $gap-sm;
  padding: 12px $gap-md;
  margin-bottom: $gap-md;
  background: rgba(68, 138, 255, 0.06);
  border: 1px solid rgba(68, 138, 255, 0.2);
  border-radius: $border-radius;

  .el-icon {
    color: $color-accent;
    font-size: 16px;
    margin-top: 2px;
  }
}

.recommendation-label {
  font-size: 13px;
  font-weight: 600;
  color: $color-accent;
  flex-shrink: 0;
}

.recommendation-text {
  font-size: 13px;
  color: $text-secondary;
  line-height: 1.5;
}

// --- Actions ---
.bottom-actions {
  display: flex;
  gap: $gap-sm;
  padding-bottom: $gap-lg;
}

// --- Loading / Empty ---
.simulation-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 400px;
  gap: $gap-md;
  color: $text-muted;
}

.simulation-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 400px;
}

// --- Report Dialog ---
.report-content {
  color: $text-secondary;
  line-height: 1.7;

  h3 {
    color: $text-primary;
    margin: 0 0 8px 0;
  }

  h4 {
    color: $text-primary;
    margin: 20px 0 8px 0;
    font-size: 14px;
  }

  p {
    margin: 0 0 8px 0;
    font-size: 13px;
  }

  ul {
    margin: 0;
    padding-left: 20px;
    font-size: 13px;
  }
}

.report-meta {
  font-size: 12px !important;
  color: $text-muted !important;
}

.report-variable,
.report-scenario {
  margin-bottom: 8px;
  font-size: 13px;
}

.report-action {
  padding: 12px;
  background: rgba(68, 138, 255, 0.06);
  border-left: 3px solid $color-accent;
  border-radius: 4px;
}

// --- Compare Drawer ---
.compare-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
</style>
