<template>
  <div class="freeze-source-bar" role="status">
    <span
      v-for="source in sources"
      :key="source.name"
      :class="['freeze-dot-group', dotClass(source)]"
      :title="tooltip(source)"
    >
      <span class="dot" />
      <span class="label">{{ label(source) }}</span>
      <span v-if="source.status === 'unavailable'" class="state-tag" data-testid="unavailable-tag">
        N/A
      </span>
      <span v-else-if="source.active" class="state-tag" data-testid="active-tag">
        冻结
      </span>
    </span>
  </div>
</template>

<script setup lang="ts">
import {
  FREEZE_SOURCE_LABELS,
  type FreezeSource,
} from '@/types/systemStatus'

interface Props {
  sources: readonly FreezeSource[]
}

const props = defineProps<Props>()

// Mark prop reference to avoid lint complaint and to keep the contract
// that we never aggregate the five into a single boolean — the template
// consumes ``sources`` directly.
void props

function dotClass(source: FreezeSource): string {
  if (source.status === 'unavailable') return 'unavailable'
  if (source.active) return 'active'
  return 'idle'
}

function label(source: FreezeSource): string {
  return FREEZE_SOURCE_LABELS[source.name]
}

function tooltip(source: FreezeSource): string {
  if (source.status === 'unavailable') {
    return `${label(source)}:探针未就绪`
  }
  if (source.active) {
    const reason = source.reason ?? '已冻结'
    return `${label(source)}:${reason}`
  }
  return `${label(source)}:正常`
}
</script>

<style lang="scss" scoped>
.freeze-source-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: $text-secondary;
}

.freeze-dot-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.idle .dot {
  background: $status-green;
  box-shadow: 0 0 4px $status-green;
}
.active .dot {
  background: $status-red;
  box-shadow: 0 0 4px $status-red;
}
.unavailable .dot {
  background: $text-muted;
  box-shadow: none;
  opacity: 0.5;
}

.label {
  font-size: 12px;
}

.state-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: rgba(255, 23, 68, 0.12);
  color: $status-red;
}

.unavailable .state-tag {
  background: rgba(142, 142, 160, 0.15);
  color: $text-muted;
}
</style>
