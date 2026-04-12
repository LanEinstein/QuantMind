<template>
  <div class="inflection-timeline">
    <div
      v-for="(item, idx) in enrichedPoints"
      :key="idx"
      class="timeline-item"
      role="button"
      tabindex="0"
      :aria-label="`Day ${item.day}: ${item.event}`"
      @click="emit('seek', item.day)"
      @keydown.enter="emit('seek', item.day)"
      @keydown.space.prevent="emit('seek', item.day)"
    >
      <div class="timeline-marker">
        <div
          class="marker-dot"
          :class="dotClass(item.inflection_type)"
          :style="dotStyle(item.confidence)"
          @mouseenter="hoveredIdx = idx"
          @mouseleave="hoveredIdx = null"
        />
        <div v-if="idx < enrichedPoints.length - 1" class="marker-line" />
      </div>
      <div class="timeline-content">
        <div class="content-header">
          <el-tag :type="typeTagType(item.inflection_type)" size="small" effect="dark">
            Day {{ item.day }}
          </el-tag>
          <span class="shift-indicator" :class="dotClass(item.inflection_type)">
            {{ shiftArrow(item.inflection_type) }} {{ shiftLabel(item.inflection_type) }}
          </span>
        </div>
        <p class="event-text">{{ item.event }}</p>
        <div class="sentiment-snapshot">
          <MiniSentimentDonut
            v-if="hoveredIdx === idx && hasSentiment(item.before_sentiment)"
            :data="item.before_sentiment"
            class="mini-donut"
          />
          <span class="snapshot-arrow" v-if="hoveredIdx === idx && hasSentiment(item.before_sentiment)">⟶</span>
          <MiniSentimentDonut
            v-if="hoveredIdx === idx && hasSentiment(item.after_sentiment)"
            :data="item.after_sentiment"
            class="mini-donut"
          />
          <template v-if="hoveredIdx !== idx">
            <span class="snapshot-label">多空比:</span>
            <span class="snapshot-before">
              {{ Math.round((item.before_sentiment['bullish'] ?? 0) * 100) }}%
            </span>
            <span class="snapshot-arrow">→</span>
            <span class="snapshot-after" :class="dotClass(item.inflection_type)">
              {{ Math.round((item.after_sentiment['bullish'] ?? 0) * 100) }}%
            </span>
          </template>
        </div>
      </div>
    </div>
    <div v-if="enrichedPoints.length === 0" class="timeline-empty">
      <span>暂无拐点数据</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import type { EnrichedInflectionViewModel, InflectionType } from '@/types/simulation'
import MiniSentimentDonut from '@/components/charts/MiniSentimentDonut.vue'
import { PLAYBACK_KEY } from '@/composables/usePlayback'

const props = defineProps<{
  inflectionPoints: readonly EnrichedInflectionViewModel[]
}>()

const emit = defineEmits<{
  seek: [day: number]
}>()

const hoveredIdx = ref<number | null>(null)

const playback = inject(PLAYBACK_KEY, undefined)

const enrichedPoints = computed(() => {
  const limit = playback?.currentRound.value
  if (limit === undefined) return props.inflectionPoints
  return props.inflectionPoints.filter((ip) => ip.day <= limit)
})

function hasSentiment(s: Readonly<Record<string, number>> | undefined): boolean {
  return !!s && Object.keys(s).length > 0
}

function dotClass(type: InflectionType | undefined): string {
  switch (type) {
    case 'sentiment_reversal': return 'type-reversal'
    case 'narrative_convergence': return 'type-convergence'
    case 'cascade_trigger': return 'type-cascade'
    case 'exhaustion': return 'type-exhaustion'
    default: return 'type-unknown'
  }
}

function dotStyle(confidence: number): Record<string, string> {
  const size = Math.round(8 + confidence * 8)
  return { width: `${size}px`, height: `${size}px` }
}

function typeTagType(
  type: InflectionType | undefined,
): 'danger' | 'warning' | 'primary' | 'info' {
  switch (type) {
    case 'sentiment_reversal': return 'danger'
    case 'narrative_convergence': return 'warning'
    case 'cascade_trigger': return 'primary'
    case 'exhaustion': return 'info'
    default: return 'info'
  }
}

function shiftArrow(type: InflectionType | undefined): string {
  switch (type) {
    case 'sentiment_reversal': return '↔'
    case 'narrative_convergence': return '⟳'
    case 'cascade_trigger': return '⚡'
    case 'exhaustion': return '↓'
    default: return '→'
  }
}

function shiftLabel(type: InflectionType | undefined): string {
  switch (type) {
    case 'sentiment_reversal': return '情绪逆转'
    case 'narrative_convergence': return '叙事收敛'
    case 'cascade_trigger': return '级联触发'
    case 'exhaustion': return '情绪耗竭'
    default: return '拐点'
  }
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
  cursor: pointer;

  &:hover .timeline-content {
    opacity: 1;
  }
}

.timeline-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 20px;
}

.marker-dot {
  border-radius: 50%;
  border: 2px solid $text-muted;
  background: $bg-card;
  flex-shrink: 0;
  transition: transform 0.15s ease;

  &.type-reversal {
    border-color: $status-red;
    background: rgba(255, 23, 68, 0.2);
  }

  &.type-convergence {
    border-color: $color-flat;
    background: rgba(255, 214, 0, 0.2);
  }

  &.type-cascade {
    border-color: $color-accent;
    background: rgba(68, 138, 255, 0.2);
  }

  &.type-exhaustion {
    border-color: $text-muted;
    background: rgba(97, 97, 97, 0.2);
  }

  .timeline-item:hover & {
    transform: scale(1.3);
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
  opacity: 0.85;
  transition: opacity 0.15s ease;
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

  &.type-reversal {
    color: $status-red;
  }

  &.type-convergence {
    color: $color-flat;
  }

  &.type-cascade {
    color: $color-accent;
  }

  &.type-exhaustion,
  &.type-unknown {
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
  min-height: 36px;
}

.mini-donut {
  flex-shrink: 0;
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

  &.type-reversal {
    color: $status-red;
  }

  &.type-convergence {
    color: $color-flat;
  }

  &.type-cascade {
    color: $color-accent;
  }

  &.type-exhaustion,
  &.type-unknown {
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
