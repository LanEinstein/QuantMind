<template>
  <div class="debate-panel">
    <!-- Current round display -->
    <template v-if="latestRound">
      <el-row :gutter="16">
        <el-col :span="12">
          <AgentCard
            v-if="latestRound.bull"
            :argument="latestRound.bull"
          />
          <div v-else class="agent-placeholder role-bull">
            <div v-if="thinkingAgent === 'bull_researcher'" class="thinking-indicator">
              <span class="thinking-icon">\uD83D\uDFE2</span>
              <span class="thinking-text">看多研究员思考中</span>
              <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
            </div>
            <span v-else class="placeholder-text">等待看多研究员发言...</span>
          </div>
        </el-col>
        <el-col :span="12">
          <AgentCard
            v-if="latestRound.bear"
            :argument="latestRound.bear"
          />
          <div v-else class="agent-placeholder role-bear">
            <div v-if="thinkingAgent === 'bear_researcher'" class="thinking-indicator">
              <span class="thinking-icon">\uD83D\uDD34</span>
              <span class="thinking-text">看空研究员思考中</span>
              <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
            </div>
            <span v-else class="placeholder-text">等待看空研究员发言...</span>
          </div>
        </el-col>
      </el-row>
    </template>

    <div v-else class="empty-debate">
      <el-empty description="暂无辩论数据，请选择标的开始分析" :image-size="80" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DebateRound } from '@/types/agent'
import AgentCard from './AgentCard.vue'

const props = defineProps<{
  rounds: readonly DebateRound[]
  thinkingAgent: string | null
}>()

const latestRound = computed(() => {
  if (props.rounds.length === 0) return null
  return props.rounds[props.rounds.length - 1]
})
</script>

<style lang="scss" scoped>
.debate-panel {
  width: 100%;
}

.agent-placeholder {
  background: $bg-card;
  border: 1px dashed $border-color;
  border-radius: $border-radius;
  padding: 32px 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;

  &.role-bull {
    border-left: 3px solid $status-green;
  }

  &.role-bear {
    border-left: 3px solid $status-red;
  }
}

.placeholder-text {
  color: $text-muted;
  font-size: 13px;
}

.thinking-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
}

.thinking-icon {
  font-size: 16px;
}

.thinking-text {
  color: $text-secondary;
  font-size: 13px;
}

.thinking-dots {
  display: inline-flex;
  gap: 2px;

  span {
    animation: blink 1.4s infinite;
    color: $color-accent;
    font-weight: bold;

    &:nth-child(2) { animation-delay: 0.2s; }
    &:nth-child(3) { animation-delay: 0.4s; }
  }
}

@keyframes blink {
  0%, 20% { opacity: 0; }
  50% { opacity: 1; }
  100% { opacity: 0; }
}

.empty-debate {
  padding: 40px 0;
}
</style>
