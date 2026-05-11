<template>
  <div class="risk-center-layout">
    <!-- Header Status Bar (read-only per P1-5 §2) -->
    <el-card shadow="never" class="status-bar-card">
      <div class="status-bar">
        <div class="status-item">
          <span class="status-icon">{{ store.systemStatusIcon }}</span>
          <span class="status-label">系统状态:</span>
          <span :class="['status-value', statusClass]">{{ store.systemStatusLabel }}</span>
        </div>
        <div class="status-item">
          <span class="status-label">运行模式:</span>
          <span class="run-mode-label">{{ store.runModeLabel }}</span>
        </div>
        <div class="status-stats">
          <span class="stat-item">今日触发止损 <b>{{ store.riskStatus?.stop_loss_triggers_today ?? 0 }}</b>次</span>
          <span class="stat-divider">|</span>
          <span class="stat-item">今日熔断 <b>{{ store.riskStatus?.circuit_breaker_triggered ? '是' : '否' }}</b></span>
          <span class="stat-divider">|</span>
          <span class="stat-item">LLM校验拦截 <b>{{ store.riskStatus?.llm_intercepts_today ?? 0 }}</b>次</span>
        </div>
      </div>
    </el-card>

    <!-- Zone A + B: Radar (left) + Risk Config (right, read-only) -->
    <el-row :gutter="12" class="zone-ab">
      <el-col :span="12">
        <el-card shadow="never" class="radar-card">
          <template #header>
            <span class="card-title">仓位监控雷达图</span>
          </template>
          <RiskRadar v-if="store.radarData" :data="store.radarData" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" class="config-card">
          <template #header>
            <span class="card-title">风控规则配置 (只读)</span>
          </template>
          <div v-if="store.config" class="config-form">
            <div class="config-row">
              <span class="config-label">单股上限</span>
              <span class="config-value">{{ store.config.single_stock_limit }}%</span>
            </div>
            <div class="config-row">
              <span class="config-label">总仓位上限</span>
              <span class="config-value">{{ store.config.total_position_limit }}%</span>
            </div>
            <div class="config-row">
              <span class="config-label">个股止损</span>
              <span class="config-value">{{ store.config.stop_loss_threshold }}%</span>
            </div>
            <div class="config-row">
              <span class="config-label">日内熔断</span>
              <span class="config-value">{{ store.config.circuit_breaker_threshold }}%</span>
            </div>
            <div class="config-row">
              <span class="config-label">LLM超时</span>
              <span class="config-value">{{ store.config.llm_timeout_seconds }}秒</span>
            </div>
            <div class="config-row">
              <span class="config-label">LLM最大连续失败</span>
              <span class="config-value">{{ store.config.llm_max_consecutive_failures }}次</span>
            </div>
            <div class="config-row">
              <span class="config-label">价格偏离限制</span>
              <span class="config-value">{{ store.config.price_deviation_limit }}%</span>
            </div>
            <div class="config-hint">
              P0-7 红线：风控参数 runtime 不可改 + hot-reload 已禁用。修改需走 git diff + amendment + 重启。
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Zone C: Risk Event Log -->
    <el-card shadow="never" class="event-log-card">
      <template #header>
        <div class="event-log-header">
          <span class="card-title">风控事件日志</span>
          <div class="event-filters">
            <el-select
              v-model="store.eventLevelFilter"
              size="small"
              placeholder="级别"
              style="width: 120px"
              @change="onEventFilterChange"
            >
              <el-option value="all" label="全部级别" />
              <el-option value="critical" label="严重" />
              <el-option value="warning" label="警告" />
              <el-option value="info" label="信息" />
              <el-option value="success" label="正常" />
            </el-select>
            <el-date-picker
              v-model="store.eventDateFilter"
              type="date"
              size="small"
              placeholder="选择日期"
              value-format="YYYY-MM-DD"
              clearable
              @change="onEventFilterChange"
            />
          </div>
        </div>
      </template>
      <el-table
        :data="store.filteredEvents"
        stripe
        class="event-table"
        max-height="320"
      >
        <el-table-column label="时间" width="180">
          <template #default="{ row }">
            {{ formatTimestamp(row.timestamp) }}
          </template>
        </el-table-column>
        <el-table-column label="级别" width="80" align="center">
          <template #default="{ row }">
            <span class="event-level-icon">{{ getLevelIcon(row.level) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="事件描述" prop="description" min-width="300" />
        <el-table-column label="处理措施" prop="action_taken" min-width="180" />
      </el-table>
    </el-card>

    <!-- Loading overlay -->
    <div v-if="store.status === 'loading'" class="loading-overlay">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { useRiskStore } from '@/stores/risk'
import RiskRadar from '@/components/charts/RiskRadar.vue'

const store = useRiskStore()

const STATUS_CLASS_MAP: Record<string, string> = {
  normal: 'status-normal',
  warning: 'status-warning',
  circuit_breaker: 'status-critical',
}

const statusClass = computed(() => {
  const level = store.riskStatus?.system_status ?? 'normal'
  return STATUS_CLASS_MAP[level] ?? 'status-normal'
})

onMounted(async () => {
  await store.fetchAll()
})

async function onEventFilterChange() {
  await store.fetchEvents()
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function getLevelIcon(level: string): string {
  const icons: Record<string, string> = {
    info: 'ℹ️',
    warning: '⚠️',
    critical: '🔴',
    success: '✅',
  }
  return icons[level] ?? '⚪'
}
</script>

<style lang="scss" scoped>
.risk-center-layout {
  padding: $gap-md;
  position: relative;
  min-height: 100%;
}

.status-bar-card {
  margin-bottom: $gap-md;
  background: $bg-card;
  border-color: $border-color;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: $gap-lg;
  flex-wrap: wrap;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-icon {
  font-size: 16px;
}

.status-label {
  font-size: 13px;
  color: $text-muted;
}

.status-value {
  font-size: 13px;
  font-weight: 600;

  &.status-normal { color: $status-green; }
  &.status-warning { color: $status-yellow; }
  &.status-critical { color: $status-red; }
}

.run-mode-label {
  font-size: 13px;
  color: $color-accent;
  font-weight: 600;
}

.status-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.stat-item {
  font-size: 12px;
  color: $text-secondary;

  b {
    color: $text-primary;
    font-family: 'Roboto Mono', monospace;
  }
}

.stat-divider {
  color: $border-color;
}

.zone-ab {
  margin-bottom: $gap-md;
}

.radar-card {
  height: 420px;
  background: $bg-card;
  border-color: $border-color;

  :deep(.el-card__body) {
    height: calc(100% - 44px);
    padding: 8px;
  }
}

.config-card {
  height: 420px;
  background: $bg-card;
  border-color: $border-color;

  :deep(.el-card__body) {
    height: calc(100% - 44px);
    overflow-y: auto;
    padding: 8px 16px;
  }
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.config-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
}

.config-label {
  font-size: 13px;
  color: $text-muted;
}

.config-value {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
  font-family: 'Roboto Mono', monospace;
}

.config-hint {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 11px;
  color: $text-muted;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 4px;
  line-height: 1.5;
}

.event-log-card {
  background: $bg-card;
  border-color: $border-color;

  :deep(.el-card__header) {
    padding: 12px 16px;
  }
}

.event-log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.event-filters {
  display: flex;
  gap: $gap-sm;
}

.event-table {
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(255, 255, 255, 0.03);
  --el-table-row-hover-bg-color: rgba(255, 255, 255, 0.05);
  --el-table-border-color: #{$border-color};
  --el-table-text-color: #{$text-primary};
  --el-table-header-text-color: #{$text-secondary};
}

.event-level-icon {
  font-size: 16px;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba($bg-primary, 0.6);
  z-index: 10;
  border-radius: $border-radius;
}
</style>
