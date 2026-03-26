<template>
  <div class="inflection-timeline">
    <div
      v-for="(item, idx) in enrichedPoints"
      :key="idx"
      class="timeline-item"
    >
      <div class="timeline-marker">
        <div class="marker-dot" :class="'shift-' + item.shift" />
        <div v-if="idx < enrichedPoints.length - 1" class="marker-line" />
      </div>
      <div class="timeline-content">
        <div class="content-header">
          <el-tag :type="shiftTagType(item.shift)" size="small" effect="dark">
            Day {{ item.day }}
          </el-tag>
          <span class="shift-indicator" :class="'shift-' + item.shift">
            {{ shiftArrow(item.shift) }} {{ shiftLabel(item.shift) }}
          </span>
        </div>
        <p class="event-text">{{ item.event }}</p>
        <div class="sentiment-snapshot">
          <span class="snapshot-label">多空比:</span>
          <span class="snapshot-before">
            {{ Math.round(item.beforeBullish * 100) }}%
          </span>
          <span class="snapshot-arrow">→</span>
          <span class="snapshot-after" :class="'shift-' + item.shift">
            {{ Math.round(item.afterBullish * 100) }}%
          </span>
        </div>
      </div>
    </div>
    <div v-if="enrichedPoints.length === 0" class="timeline-empty">
      <span>暂无拐点数据</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { InflectionPoint, SentimentSnapshot } from '@/types/simulation'

type ShiftType = 'bullish' | 'bearish' | 'neutral'

interface EnrichedInflection {
  readonly day: number
  readonly event: string
  readonly beforeBullish: number
  readonly afterBullish: number
  readonly shift: ShiftType
}

const props = defineProps<{
  inflectionPoints: readonly InflectionPoint[]
  sentimentData: readonly SentimentSnapshot[]
}>()

function findSentiment(round: number): number {
  const snap = props.sentimentData.find((s) => s.round === round)
  return snap?.bullish ?? 0.5
}

const enrichedPoints = computed((): readonly EnrichedInflection[] => {
  return props.inflectionPoints.map((ip) => {
    const before = findSentiment(Math.max(1, ip.day - 1))
    const after = findSentiment(Math.min(props.sentimentData.length, ip.day + 1))
    const delta = after - before
    const shift: ShiftType =
      delta > 0.02 ? 'bullish' : delta < -0.02 ? 'bearish' : 'neutral'
    return {
      day: ip.day,
      event: ip.event,
      beforeBullish: before,
      afterBullish: after,
      shift,
    }
  })
})

function shiftTagType(shift: ShiftType): 'success' | 'danger' | 'info' {
  if (shift === 'bullish') return 'success'
  if (shift === 'bearish') return 'danger'
  return 'info'
}

function shiftArrow(shift: ShiftType): string {
  if (shift === 'bullish') return '\u2191'
  if (shift === 'bearish') return '\u2193'
  return '\u2192'
}

function shiftLabel(shift: ShiftType): string {
  if (shift === 'bullish') return '看多增强'
  if (shift === 'bearish') return '看空增强'
  return '情绪平稳'
}
</script>

<style scoped lang="scss">
.inflection-timeline {
  height: 100%;
  overflow-y: auto;
  padding: 4px 0;
}

.timeline-item {
  display: flex;
  gap: 12px;
}

.timeline-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 20px;
}

.marker-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid $text-muted;
  background: $bg-card;
  flex-shrink: 0;

  &.shift-bullish {
    border-color: $status-green;
    background: rgba(0, 200, 83, 0.2);
  }

  &.shift-bearish {
    border-color: $status-red;
    background: rgba(255, 23, 68, 0.2);
  }

  &.shift-neutral {
    border-color: $text-muted;
  }
}

.marker-line {
  flex: 1;
  width: 2px;
  background: $border-color;
  min-height: 24px;
}

.timeline-content {
  flex: 1;
  padding-bottom: 16px;
}

.content-header {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  margin-bottom: 6px;
}

.shift-indicator {
  font-size: 12px;
  font-weight: 500;

  &.shift-bullish {
    color: $status-green;
  }

  &.shift-bearish {
    color: $status-red;
  }

  &.shift-neutral {
    color: $text-muted;
  }
}

.event-text {
  margin: 0 0 8px 0;
  font-size: 13px;
  line-height: 1.5;
  color: $text-secondary;
}

.sentiment-snapshot {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: $text-muted;
}

.snapshot-label {
  margin-right: 2px;
}

.snapshot-before {
  font-family: monospace;
}

.snapshot-arrow {
  color: $text-muted;
}

.snapshot-after {
  font-family: monospace;
  font-weight: 600;

  &.shift-bullish {
    color: $status-green;
  }

  &.shift-bearish {
    color: $status-red;
  }

  &.shift-neutral {
    color: $text-muted;
  }
}

.timeline-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: $text-muted;
  font-size: 13px;
}
</style>
