<template>
  <div class="decision-section">
    <!-- Risk Officer -->
    <el-card shadow="never" class="risk-card" v-if="risk">
      <div class="section-header">
        <span class="section-icon">👔</span>
        <span class="section-title">风控官审核</span>
        <el-tag size="small" effect="dark">{{ risk.model }}</el-tag>
      </div>
      <div class="risk-checks">
        <span
          v-for="check in risk.checks"
          :key="check.label"
          class="risk-check"
          :class="{ passed: check.passed, failed: !check.passed }"
        >
          {{ check.label }} {{ check.passed ? '✅' : '❌' }}
        </span>
        <span class="risk-position">建议仓位: ≤{{ risk.position_limit }}</span>
      </div>
    </el-card>

    <!-- Fund Manager Decision (read-only history view) -->
    <el-card shadow="never" class="decision-card" v-if="decision">
      <div class="section-header">
        <span class="section-icon">🎯</span>
        <span class="section-title">基金经理决策</span>
        <el-tag size="small" effect="dark">{{ decision.model }}</el-tag>
      </div>

      <!-- Score Gauge -->
      <div class="score-row">
        <div class="score-gauge">
          <span class="score-label">综合评分:</span>
          <div class="gauge-bar">
            <div
              class="gauge-fill"
              :style="{ width: decision.score + '%', background: scoreColor }"
            />
          </div>
          <span class="score-value" :style="{ color: scoreColor }">
            {{ decision.score }}/100
          </span>
          <el-tag :type="scoreTagType" size="small" effect="dark">
            {{ decision.score_label }}
          </el-tag>
        </div>
      </div>

      <!-- Decision Details -->
      <div class="decision-details">
        <div class="detail-grid">
          <div class="detail-item">
            <span class="detail-label">操作</span>
            <span class="detail-value" :class="actionClass">{{ decision.action }}</span>
          </div>
          <div class="detail-item" v-if="decision.target_price">
            <span class="detail-label">目标价</span>
            <span class="detail-value">¥{{ decision.target_price }}</span>
          </div>
          <div class="detail-item" v-if="decision.stop_loss">
            <span class="detail-label">止损价</span>
            <span class="detail-value">¥{{ decision.stop_loss }}</span>
          </div>
          <div class="detail-item" v-if="decision.position_pct">
            <span class="detail-label">仓位</span>
            <span class="detail-value">{{ decision.position_pct }}%</span>
          </div>
        </div>
        <p class="decision-reasoning">{{ decision.reasoning }}</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RiskAssessment, FundManagerDecision } from '@/types/agent'

const props = defineProps<{
  risk: RiskAssessment | null
  decision: FundManagerDecision | null
}>()

const scoreColor = computed(() => {
  if (!props.decision) return '#999'
  const s = props.decision.score
  if (s >= 70) return '#00c853'
  if (s >= 40) return '#ffd600'
  return '#ff1744'
})

const scoreTagType = computed(() => {
  if (!props.decision) return 'info'
  const s = props.decision.score
  if (s >= 70) return 'success'
  if (s >= 40) return 'warning'
  return 'danger'
})

const actionClass = computed(() => {
  if (!props.decision) return ''
  const map: Record<string, string> = {
    '买入': 'action-buy',
    '卖出': 'action-sell',
    '持有': 'action-hold',
  }
  return map[props.decision.action] ?? ''
})
</script>

<style lang="scss" scoped>
.decision-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.section-icon {
  font-size: 18px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.risk-checks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.risk-check {
  font-size: 13px;

  &.passed { color: $status-green; }
  &.failed { color: $status-red; }
}

.risk-position {
  font-size: 13px;
  color: $color-accent;
  font-weight: 600;
  margin-left: auto;
}

.score-row {
  margin-bottom: 16px;
}

.score-gauge {
  display: flex;
  align-items: center;
  gap: 10px;
}

.score-label {
  font-size: 13px;
  color: $text-secondary;
  white-space: nowrap;
}

.gauge-bar {
  flex: 1;
  height: 12px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  overflow: hidden;
}

.gauge-fill {
  height: 100%;
  border-radius: 6px;
  transition: width 0.6s ease;
}

.score-value {
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-label {
  font-size: 11px;
  color: $text-muted;
}

.detail-value {
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;

  &.action-buy { color: $color-up; }
  &.action-sell { color: $color-down; }
  &.action-hold { color: $color-flat; }
}

.decision-reasoning {
  font-size: 12px;
  color: $text-secondary;
  line-height: 1.6;
  margin: 0;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
}
</style>
