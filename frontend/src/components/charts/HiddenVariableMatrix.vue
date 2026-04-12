<template>
  <div class="hidden-variable-matrix">
    <el-collapse v-model="expandedVars">
      <el-collapse-item
        v-for="(v, idx) in sortedVariables"
        :key="idx"
        :name="idx"
      >
        <template #title>
          <div
            class="var-header"
            :class="{ 'var-absent': v.is_absent_from_original }"
          >
            <span class="var-name" :title="v.variable">{{ v.variable }}</span>
            <div class="bar-container">
              <div
                class="bar-fill-consensus"
                :style="{ width: ((v.agent_consensus_ratio ?? 0) * 100) + '%' }"
              />
              <div
                class="bar-fill"
                :style="{
                  width: (v.probability * 100) + '%',
                  backgroundColor: barColor(v.probability),
                }"
              />
            </div>
            <span class="var-pct" :style="{ color: barColor(v.probability) }">
              {{ Math.round(v.probability * 100) }}%
            </span>
          </div>
        </template>
        <div class="var-body">
          <p class="reasoning-text">{{ v.reasoning }}</p>
          <div class="disclaimer">
            <el-icon><WarningFilled /></el-icon>
            <span>此概率为群体仿真估计，非统计概率，仅供决策参考</span>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { WarningFilled } from '@element-plus/icons-vue'
import type { HiddenVariable } from '@/types/simulation'

const props = defineProps<{
  variables: readonly HiddenVariable[]
}>()

const expandedVars = ref<number[]>([])

const sortedVariables = computed(() =>
  [...props.variables].sort((a, b) => {
    const diff = b.probability - a.probability
    if (diff !== 0) return diff
    return (b.agent_consensus_ratio ?? 0) - (a.agent_consensus_ratio ?? 0)
  }),
)

function barColor(probability: number): string {
  if (probability >= 0.7) return '#00c853'
  if (probability >= 0.4) return '#ffd600'
  return '#616161'
}
</script>

<style scoped lang="scss">
.hidden-variable-matrix {
  height: 100%;
  overflow-y: auto;
}

:deep(.el-collapse) {
  border-top: none;
  border-bottom: none;
}

:deep(.el-collapse-item__header) {
  background: transparent;
  border-bottom-color: $border-color;
  color: $text-primary;
  font-size: 13px;
  height: 44px;
  line-height: 44px;
  padding: 0;
}

:deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom-color: $border-color;
}

:deep(.el-collapse-item__content) {
  padding-bottom: 12px;
  color: $text-secondary;
}

.var-header {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  width: 100%;
  padding-right: 8px;
  padding-left: 0;
  border-left: 2px solid transparent;
  transition: border-color 0.2s ease;

  &.var-absent {
    border-left: 2px dashed $color-flat;
  }
}

.var-name {
  flex-shrink: 0;
  width: 140px;
  font-size: 13px;
  color: $text-primary;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bar-container {
  flex: 1;
  position: relative;
  height: 16px;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 8px;
  overflow: hidden;
}

.bar-fill-consensus {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  background: rgba(0, 200, 83, 0.3);
  border-radius: 8px;
  transition: width 0.6s ease;
  z-index: 0;
}

.bar-fill {
  position: relative;
  height: 100%;
  border-radius: 8px;
  transition: width 0.6s ease;
  z-index: 1;
}

.var-pct {
  flex-shrink: 0;
  width: 40px;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  font-family: monospace;
}

.var-body {
  padding: 8px 0 0 0;
}

.reasoning-text {
  margin: 0 0 12px 0;
  font-size: 12px;
  line-height: 1.6;
  color: $text-secondary;
}

.disclaimer {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(255, 214, 0, 0.06);
  border-left: 2px solid $color-flat;
  border-radius: 4px;
  font-size: 11px;
  color: $text-muted;

  .el-icon {
    color: $color-flat;
    font-size: 14px;
  }
}
</style>
