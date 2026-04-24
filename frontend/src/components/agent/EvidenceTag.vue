<template>
  <el-popover
    placement="bottom"
    :width="280"
    trigger="click"
    :teleported="true"
  >
    <template #reference>
      <span class="evidence-tag" :class="statusClass">
        <span class="evidence-label">{{ item.label }}</span>
        <span class="evidence-model">({{ item.model }})</span>
        <span class="evidence-icon">{{ statusIcon }}</span>
      </span>
    </template>
    <div class="evidence-detail">
      <div class="evidence-detail-header">
        <span class="evidence-detail-label">{{ item.label }}</span>
        <el-tag :type="tagType" size="small" effect="dark">{{ item.model }}</el-tag>
      </div>
      <p class="evidence-detail-text">{{ item.detail }}</p>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { EvidenceItem } from '@/types/agent'

const props = defineProps<{
  item: EvidenceItem
}>()

const statusClass = computed(() => `status-${props.item.status}`)

const statusIcon = computed(() => {
  const icons: Record<string, string> = {
    positive: '\u2705',
    mixed: '\u26A0\uFE0F',
    negative: '\u274C',
  }
  return icons[props.item.status] ?? ''
})

type ElTagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

const tagType = computed((): ElTagType => {
  const types: Record<string, ElTagType> = {
    DeepSeek: 'success',
    Qwen: 'warning',
    Kimi: 'primary',
    MiroFish: 'info',
  }
  return types[props.item.model] ?? 'primary'
})
</script>

<style lang="scss" scoped>
.evidence-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  transition: opacity 0.2s;
  user-select: none;

  &:hover {
    opacity: 0.8;
  }

  &.status-positive {
    background: rgba(0, 200, 83, 0.12);
    color: #69f0ae;
  }

  &.status-mixed {
    background: rgba(255, 214, 0, 0.12);
    color: #ffd740;
  }

  &.status-negative {
    background: rgba(255, 23, 68, 0.12);
    color: #ff5252;
  }
}

.evidence-model {
  color: $text-muted;
}

.evidence-detail {
  &-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  &-label {
    font-weight: 600;
    font-size: 13px;
    color: $text-primary;
  }

  &-text {
    font-size: 12px;
    color: $text-secondary;
    line-height: 1.6;
    margin: 0;
  }
}
</style>
