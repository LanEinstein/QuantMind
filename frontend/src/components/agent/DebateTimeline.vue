<template>
  <div class="debate-timeline">
    <!-- Progress bar -->
    <div class="timeline-progress">
      <span class="progress-label">
        辩论轮次:
      </span>
      <el-progress
        :percentage="progressPct"
        :stroke-width="18"
        :text-inside="true"
        :format="() => `Round ${currentRound}/${maxRounds}`"
        class="round-progress"
      />
    </div>

    <!-- Expandable round history -->
    <el-collapse v-model="expandedRounds" class="round-collapse">
      <el-collapse-item
        v-for="round in rounds"
        :key="round.round"
        :name="round.round"
      >
        <template #title>
          <div class="round-title">
            <el-tag size="small" effect="dark" class="round-tag">
              Round {{ round.round }}
            </el-tag>
            <span class="round-summary" v-if="round.bull">
              看多: {{ truncate(round.bull.content, 40) }}
            </span>
            <span class="round-vs" v-if="round.bull && round.bear">vs</span>
            <span class="round-summary" v-if="round.bear">
              看空: {{ truncate(round.bear.content, 40) }}
            </span>
          </div>
        </template>

        <div class="round-detail">
          <el-row :gutter="12">
            <el-col :span="12">
              <div class="round-argument bull" v-if="round.bull">
                <div class="arg-header">
                  <span>\uD83D\uDFE2 看多研究员</span>
                  <el-tag size="small" effect="plain">{{ round.bull.model }}</el-tag>
                </div>
                <p class="arg-text">{{ round.bull.content }}</p>
                <div class="arg-evidence">
                  <EvidenceTag
                    v-for="(ev, idx) in round.bull.evidence"
                    :key="idx"
                    :item="ev"
                  />
                </div>
              </div>
              <div v-else class="round-argument empty">看多研究员未发言</div>
            </el-col>
            <el-col :span="12">
              <div class="round-argument bear" v-if="round.bear">
                <div class="arg-header">
                  <span>\uD83D\uDD34 看空研究员</span>
                  <el-tag size="small" effect="plain">{{ round.bear.model }}</el-tag>
                </div>
                <p class="arg-text">{{ round.bear.content }}</p>
                <div class="arg-evidence">
                  <EvidenceTag
                    v-for="(ev, idx) in round.bear.evidence"
                    :key="idx"
                    :item="ev"
                  />
                </div>
              </div>
              <div v-else class="round-argument empty">看空研究员未发言</div>
            </el-col>
          </el-row>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { DebateRound } from '@/types/agent'
import EvidenceTag from './EvidenceTag.vue'

const props = defineProps<{
  rounds: readonly DebateRound[]
  currentRound: number
  maxRounds: number
}>()

const expandedRounds = ref<number[]>([])

const progressPct = computed(() => {
  if (props.maxRounds <= 0) return 0
  return Math.min(100, Math.round((props.currentRound / props.maxRounds) * 100))
})

function truncate(text: string, maxLen: number): string {
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
}
</script>

<style lang="scss" scoped>
.debate-timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.timeline-progress {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-label {
  font-size: 13px;
  color: $text-secondary;
  white-space: nowrap;
}

.round-progress {
  flex: 1;

  :deep(.el-progress-bar__inner) {
    background: linear-gradient(90deg, $color-accent, $color-accent-light);
  }

  :deep(.el-progress-bar__innerText) {
    font-size: 11px;
  }
}

.round-collapse {
  border: none;

  :deep(.el-collapse-item__header) {
    background: transparent;
    border-bottom-color: $border-color;
    color: $text-primary;
    font-size: 13px;
    height: 40px;
    line-height: 40px;
  }

  :deep(.el-collapse-item__wrap) {
    background: transparent;
    border-bottom-color: $border-color;
  }

  :deep(.el-collapse-item__content) {
    padding-bottom: 12px;
    color: $text-secondary;
  }
}

.round-title {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.round-tag {
  flex-shrink: 0;
}

.round-summary {
  font-size: 12px;
  color: $text-muted;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.round-vs {
  font-size: 11px;
  color: $text-muted;
  flex-shrink: 0;
}

.round-argument {
  padding: 12px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.03);

  &.bull {
    border-left: 2px solid $status-green;
  }

  &.bear {
    border-left: 2px solid $status-red;
  }

  &.empty {
    color: $text-muted;
    font-size: 12px;
    text-align: center;
    padding: 20px;
  }
}

.arg-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}

.arg-text {
  font-size: 12px;
  color: $text-secondary;
  line-height: 1.6;
  margin: 0 0 8px;
}

.arg-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
</style>
