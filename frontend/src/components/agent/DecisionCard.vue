<template>
  <div class="decision-section">
    <!-- Risk Officer -->
    <el-card shadow="never" class="risk-card" v-if="risk">
      <div class="section-header">
        <span class="section-icon">\uD83D\uDC54</span>
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
          {{ check.label }} {{ check.passed ? '\u2705' : '\u274C' }}
        </span>
        <span class="risk-position">建议仓位: \u2264{{ risk.position_limit }}</span>
      </div>
    </el-card>

    <!-- Fund Manager Decision -->
    <el-card shadow="never" class="decision-card" v-if="decision">
      <div class="section-header">
        <span class="section-icon">\uD83C\uDFAF</span>
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
            <span class="detail-value">\u00A5{{ decision.target_price }}</span>
          </div>
          <div class="detail-item" v-if="decision.stop_loss">
            <span class="detail-label">止损价</span>
            <span class="detail-value">\u00A5{{ decision.stop_loss }}</span>
          </div>
          <div class="detail-item" v-if="decision.position_pct">
            <span class="detail-label">仓位</span>
            <span class="detail-value">{{ decision.position_pct }}%</span>
          </div>
        </div>
        <p class="decision-reasoning">{{ decision.reasoning }}</p>
      </div>

      <!-- Authorization Mode Buttons -->
      <div class="auth-buttons">
        <el-button-group>
          <el-button
            :type="authMode === 'suggest' ? 'success' : 'default'"
            size="small"
            @click="$emit('auth-change', 'suggest')"
          >
            \uD83D\uDFE2 建议模式
          </el-button>
          <el-button
            :type="authMode === 'confirm' ? 'warning' : 'default'"
            size="small"
            @click="$emit('auth-change', 'confirm')"
          >
            \uD83D\uDFE1 确认模式
          </el-button>
          <el-button
            :type="authMode === 'auto' ? 'danger' : 'default'"
            size="small"
            @click="$emit('auth-change', 'auto')"
          >
            \uD83D\uDD34 自动模式
          </el-button>
        </el-button-group>

        <div class="auth-action" v-if="authMode === 'suggest'">
          <el-button type="info" size="small" disabled>仅展示</el-button>
        </div>
        <div class="auth-action" v-else-if="authMode === 'confirm'">
          <el-button type="success" size="small" @click="$emit('approve')">
            \u2705 批准执行
          </el-button>
          <el-button type="danger" size="small" @click="$emit('reject')">
            \u274C 拒绝
          </el-button>
        </div>
        <div class="auth-action" v-else>
          <el-tag type="warning" effect="dark">已自动提交</el-tag>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { RiskAssessment, FundManagerDecision, AuthMode } from '@/types/agent'

const props = defineProps<{
  risk: RiskAssessment | null
  decision: FundManagerDecision | null
  authMode: AuthMode
}>()

defineEmits<{
  'auth-change': [mode: AuthMode]
  approve: []
  reject: []
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

// Risk card
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

// Score gauge
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

// Decision details
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

// Auth buttons
.auth-buttons {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid $border-color;
}

.auth-action {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
