<template>
  <div class="risk-center-layout">
    <!-- Header Status Bar -->
    <el-card shadow="never" class="status-bar-card">
      <div class="status-bar">
        <div class="status-item">
          <span class="status-icon">{{ store.systemStatusIcon }}</span>
          <span class="status-label">系统状态:</span>
          <span :class="['status-value', statusClass]">{{ store.systemStatusLabel }}</span>
        </div>
        <div class="status-item">
          <span class="status-label">授权模式:</span>
          <el-dropdown trigger="click" @command="onSwitchAuthMode">
            <span class="auth-mode-btn">
              {{ store.authModeLabel }}
              <el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="suggestion">建议模式</el-dropdown-item>
                <el-dropdown-item command="semi_auto">半自动模式</el-dropdown-item>
                <el-dropdown-item command="full_auto">全自动模式</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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

    <!-- Zone A + B: Radar (left) + Risk Config (right) -->
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
            <span class="card-title">风控规则配置</span>
          </template>
          <div v-if="editConfig" class="config-form">
            <div class="config-row">
              <span class="config-label">单股上限</span>
              <div class="config-controls">
                <el-slider
                  v-model="editConfig.single_stock_limit"
                  :min="5"
                  :max="50"
                  :step="1"
                  class="config-slider"
                />
                <el-input-number
                  v-model="editConfig.single_stock_limit"
                  :min="5"
                  :max="50"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">%</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('single_stock_limit')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">总仓位上限</span>
              <div class="config-controls">
                <el-slider
                  v-model="editConfig.total_position_limit"
                  :min="20"
                  :max="100"
                  :step="5"
                  class="config-slider"
                />
                <el-input-number
                  v-model="editConfig.total_position_limit"
                  :min="20"
                  :max="100"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">%</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('total_position_limit')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">个股止损</span>
              <div class="config-controls">
                <el-slider
                  v-model="editConfig.stop_loss_threshold"
                  :min="-20"
                  :max="-1"
                  :step="1"
                  class="config-slider"
                />
                <el-input-number
                  v-model="editConfig.stop_loss_threshold"
                  :min="-20"
                  :max="-1"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">%</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('stop_loss_threshold')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">日内熔断</span>
              <div class="config-controls">
                <el-slider
                  v-model="editConfig.circuit_breaker_threshold"
                  :min="-10"
                  :max="-1"
                  :step="0.5"
                  class="config-slider"
                />
                <el-input-number
                  v-model="editConfig.circuit_breaker_threshold"
                  :min="-10"
                  :max="-1"
                  :step="0.5"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">%</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('circuit_breaker_threshold')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">LLM超时</span>
              <div class="config-controls">
                <el-input-number
                  v-model="editConfig.llm_timeout_seconds"
                  :min="5"
                  :max="120"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">秒</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('llm_timeout_seconds')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">LLM最大连续失败</span>
              <div class="config-controls">
                <el-input-number
                  v-model="editConfig.llm_max_consecutive_failures"
                  :min="1"
                  :max="10"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">次</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('llm_max_consecutive_failures')"
                >保存</el-button>
              </div>
            </div>

            <div class="config-row">
              <span class="config-label">价格偏离限制</span>
              <div class="config-controls">
                <el-slider
                  v-model="editConfig.price_deviation_limit"
                  :min="1"
                  :max="20"
                  :step="0.5"
                  class="config-slider"
                />
                <el-input-number
                  v-model="editConfig.price_deviation_limit"
                  :min="1"
                  :max="20"
                  :step="0.5"
                  size="small"
                  controls-position="right"
                />
                <span class="config-unit">%</span>
                <el-button
                  size="small"
                  type="primary"
                  @click="onSaveConfig('price_deviation_limit')"
                >保存</el-button>
              </div>
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
import { reactive, computed, onMounted, watch } from 'vue'
import { Loading, ArrowDown } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRiskStore } from '@/stores/risk'
import type { RiskConfig, AuthorizationMode } from '@/types/risk'
import RiskRadar from '@/components/charts/RiskRadar.vue'

const store = useRiskStore()

// Local mutable copy of config for the form
const editConfig = reactive<RiskConfig>({
  single_stock_limit: 20,
  total_position_limit: 80,
  stop_loss_threshold: -8,
  circuit_breaker_threshold: -3,
  llm_timeout_seconds: 30,
  llm_max_consecutive_failures: 3,
  price_deviation_limit: 5,
})

// Sync edit config when store config loads
watch(
  () => store.config,
  (cfg) => {
    if (cfg) {
      Object.assign(editConfig, cfg)
    }
  },
  { immediate: true },
)

const statusClass = computed(() => {
  const level = store.riskStatus?.system_status ?? 'normal'
  return {
    normal: 'status-normal',
    warning: 'status-warning',
    circuit_breaker: 'status-critical',
  }[level]
})

onMounted(async () => {
  await store.fetchAll()
})

async function onSaveConfig(field: keyof RiskConfig) {
  try {
    await ElMessageBox.confirm(
      '修改风控参数将立即生效，确认？',
      '风控参数修改',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' },
    )
    await store.updateConfig({ [field]: editConfig[field] })
    ElMessage.success('参数已更新')
    await store.fetchRadarData()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('更新失败')
    }
  }
}

async function onSwitchAuthMode(mode: string) {
  const labels: Record<string, string> = {
    suggestion: '建议模式',
    semi_auto: '半自动模式',
    full_auto: '全自动模式',
  }
  try {
    await ElMessageBox.confirm(
      `确认切换授权模式为 "${labels[mode]}"？\n此操作将立即生效。`,
      '授权模式切换',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' },
    )
    await store.switchAuthMode(mode as AuthorizationMode)
    ElMessage.success(`已切换至${labels[mode]}`)
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('模式切换失败')
    }
  }
}

async function onEventFilterChange() {
  await store.fetchEvents()
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts)
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

// Header status bar
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

.auth-mode-btn {
  font-size: 13px;
  color: $color-accent;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 2px;

  &:hover { color: $color-accent-light; }
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

// Zone A+B
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

// Config form
.config-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.config-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-label {
  font-size: 12px;
  color: $text-muted;
  font-weight: 500;
}

.config-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.config-slider {
  flex: 1;
  min-width: 80px;
}

.config-unit {
  font-size: 12px;
  color: $text-muted;
  min-width: 16px;
}

// Event log
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
