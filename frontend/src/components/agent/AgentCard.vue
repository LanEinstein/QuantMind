<template>
  <div class="agent-card" :class="roleClass">
    <div class="agent-header">
      <span class="agent-role-icon">{{ roleIcon }}</span>
      <span class="agent-role-name">{{ roleName }}</span>
      <el-tag size="small" effect="dark" :type="modelTagType" class="model-tag">
        {{ argument.model }}
      </el-tag>
    </div>

    <div class="agent-content">
      <p class="argument-text">{{ argument.content }}</p>
    </div>

    <div class="agent-evidence" v-if="argument.evidence.length > 0">
      <div class="evidence-label-row">论据:</div>
      <div class="evidence-list">
        <EvidenceTag
          v-for="(ev, idx) in argument.evidence"
          :key="idx"
          :item="ev"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DebateArgument } from '@/types/agent'
import EvidenceTag from './EvidenceTag.vue'

const props = defineProps<{
  argument: DebateArgument
}>()

const roleClass = computed(() => `role-${props.argument.role}`)

const roleIcon = computed(() =>
  props.argument.role === 'bull' ? '\uD83D\uDFE2' : '\uD83D\uDD34',
)

const roleName = computed(() =>
  props.argument.role === 'bull' ? '看多研究员' : '看空研究员',
)

type ElTagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

const modelTagType = computed((): ElTagType => {
  const types: Record<string, ElTagType> = {
    MiniMax: 'primary',
    DeepSeek: 'success',
    Qwen: 'warning',
  }
  return types[props.argument.model] ?? 'info'
})
</script>

<style lang="scss" scoped>
.agent-card {
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;

  &.role-bull {
    border-left: 3px solid $status-green;
  }

  &.role-bear {
    border-left: 3px solid $status-red;
  }
}

.agent-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-role-icon {
  font-size: 16px;
}

.agent-role-name {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.model-tag {
  margin-left: auto;
}

.agent-content {
  flex: 1;
}

.argument-text {
  font-size: 13px;
  color: $text-secondary;
  line-height: 1.7;
  margin: 0;
}

.evidence-label-row {
  font-size: 12px;
  color: $text-muted;
  margin-bottom: 6px;
}

.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
