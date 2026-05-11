<template>
  <div class="status-bar">
    <!-- LLM connection status -->
    <div class="status-group">
      <span class="status-label">LLM</span>
      <span :class="['status-dot', status.deepseek ? 'on' : 'off']" title="DeepSeek" />
      <span class="status-name">DeepSeek</span>
      <span :class="['status-dot', status.qwen ? 'on' : 'off']" title="Qwen" />
      <span class="status-name">Qwen</span>
      <span :class="['status-dot', status.kimi ? 'on' : 'off']" title="Kimi" />
      <span class="status-name">Kimi</span>
    </div>

    <div class="status-divider" />

    <!-- Data source status -->
    <div class="status-group">
      <span class="status-label">数据</span>
      <span :class="['status-dot', status.adata ? 'on' : 'off']" />
      <span class="status-name">adata</span>
      <span :class="['status-dot', status.akshare ? 'on' : 'off']" />
      <span class="status-name">AKShare</span>
    </div>

    <div class="status-divider" />

    <!-- Daily cost -->
    <div class="status-group">
      <span class="status-label">今日成本</span>
      <span class="status-cost">¥{{ status.daily_cost_rmb.toFixed(1) }}</span>
    </div>

    <div class="status-divider" />

    <!-- Risk engine -->
    <div class="status-group">
      <span class="status-label">风控</span>
      <span :class="['status-dot', riskDotClass]" />
      <span class="status-name">{{ riskLabel }}</span>
    </div>

    <div class="status-divider" />

    <!-- Run mode (P0-1) -->
    <div class="status-group">
      <span class="status-label">模式</span>
      <span :class="['status-dot', runModeDotClass]" />
      <span class="status-name">{{ runModeLabel }}</span>
    </div>

    <!-- WebSocket status -->
    <div class="status-group ws-status">
      <span :class="['status-dot', wsConnected ? 'on' : 'off']" />
      <span class="status-name">WS</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SystemStatus } from '@/types/market'

const props = withDefaults(defineProps<{
  status: SystemStatus
  wsConnected: boolean
}>(), {
  wsConnected: false,
})

const riskDotClass = computed(() => {
  if (props.status.risk_status === 'normal') return 'on'
  if (props.status.risk_status === 'warning') return 'warn'
  return 'off'
})

const riskLabel = computed(() => {
  const labels: Record<string, string> = { normal: '正常', warning: '预警', halt: '熔断' }
  return labels[props.status.risk_status] ?? '未知'
})

// P0-1: simulation_auto is always-on; the dot only changes when the
// feishu_interactive overlay is layered on (warn) so the operator can
// see the human-in-loop path is active.
const runModeDotClass = computed(() =>
  props.status.feishu_interactive ? 'warn' : 'on',
)

const runModeLabel = computed(() =>
  props.status.feishu_interactive ? '模拟+飞书' : '模拟',
)
</script>

<style lang="scss" scoped>
.status-bar {
  height: $status-bar-height;
  background: $bg-status-bar;
  border-top: 1px solid $border-color;
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 6px;
  font-size: 12px;
  color: $text-muted;
  flex-shrink: 0;
}

.status-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

.ws-status {
  margin-left: auto;
}

.status-label {
  color: $text-secondary;
  margin-right: 2px;
}

.status-name {
  color: $text-muted;
  font-size: 11px;
}

.status-cost {
  color: $color-accent-light;
  font-family: 'Roboto Mono', monospace;
  font-weight: 600;
}

.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;

  &.on { background: $status-green; box-shadow: 0 0 4px $status-green; }
  &.warn { background: $status-yellow; box-shadow: 0 0 4px $status-yellow; }
  &.off { background: $status-red; box-shadow: 0 0 4px $status-red; }
}

.status-divider {
  width: 1px;
  height: 16px;
  background: $border-color;
  margin: 0 4px;
}
</style>
