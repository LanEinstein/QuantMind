<template>
  <div class="model-contribution">
    <div v-for="model in data" :key="model.model" class="model-row">
      <div class="model-header">
        <span class="model-name">{{ model.label }}</span>
        <span class="model-accuracy">
          {{ model.accuracy_label }}
          <span :class="['accuracy-value', getAccuracyClass(model.accuracy_value)]">
            {{ (model.accuracy_value * 100).toFixed(0) }}%
          </span>
        </span>
      </div>
      <div class="accuracy-bar">
        <div
          class="accuracy-fill"
          :class="getAccuracyClass(model.accuracy_value)"
          :style="{ width: (model.accuracy_value * 100) + '%' }"
        />
      </div>
      <div class="model-stats">
        <span class="stat">
          {{ model.call_label }}: {{ model.call_value }}{{ model.call_unit }}
        </span>
        <span class="stat">
          {{ model.cost_label }}: {{ model.cost_unit }}{{ model.cost_value }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { ModelMetric } from '@/types/performance'

defineProps<{
  data: readonly ModelMetric[]
}>()

function getAccuracyClass(value: number): string {
  if (value >= 0.65) return 'high'
  if (value >= 0.55) return 'mid'
  return 'low'
}
</script>

<style lang="scss" scoped>
.model-contribution {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.model-row {
  padding: 8px 0;
  border-bottom: 1px solid $border-color;

  &:last-child {
    border-bottom: none;
  }
}

.model-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.model-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}

.model-accuracy {
  font-size: 12px;
  color: $text-muted;
}

.accuracy-value {
  font-weight: 700;
  margin-left: 4px;

  &.high { color: $color-down; } // green = good in this context
  &.mid { color: $color-flat; }
  &.low { color: $text-muted; }
}

.accuracy-bar {
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 2px;
  margin-bottom: 6px;
  overflow: hidden;
}

.accuracy-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.6s ease;

  &.high { background: $color-down; } // green
  &.mid { background: $color-flat; }
  &.low { background: $text-muted; }
}

.model-stats {
  display: flex;
  gap: 16px;
}

.stat {
  font-size: 11px;
  color: $text-muted;
  font-family: 'Roboto Mono', monospace;
}
</style>
