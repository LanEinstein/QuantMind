<template>
  <div class="news-feed">
    <div
      v-for="(article, idx) in articles"
      :key="idx"
      :class="['news-item', importanceClass(article.importance_score)]"
      @click="toggleExpand(idx)"
    >
      <div class="news-header">
        <span v-if="article.has_simulation" class="sim-icon" title="已完成MiroFish仿真">🔮</span>
        <span class="news-title">{{ article.title }}</span>
        <span class="news-meta">
          <span class="news-source">{{ article.source }}</span>
          <span class="news-time">{{ formatTime(article.publish_time) }}</span>
          <span :class="['importance-badge', importanceClass(article.importance_score)]">
            {{ article.importance_score }}
          </span>
        </span>
      </div>
      <div v-if="expandedIdx === idx" class="news-body">
        <p>{{ article.content }}</p>
        <p v-if="article.simulation_summary" class="sim-summary">
          🔮 仿真摘要: {{ article.simulation_summary }}
        </p>
      </div>
    </div>
    <div v-if="articles.length === 0" class="news-empty">暂无新闻数据</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import dayjs from 'dayjs'
import type { NewsArticle } from '@/types/market'

defineProps<{
  articles: NewsArticle[]
}>()

const expandedIdx = ref<number | null>(null)

function toggleExpand(idx: number) {
  expandedIdx.value = expandedIdx.value === idx ? null : idx
}

function importanceClass(score: number): string {
  if (score >= 7) return 'high'
  if (score >= 4) return 'mid'
  return 'low'
}

function formatTime(ts: string): string {
  return dayjs(ts).format('HH:mm')
}
</script>

<style lang="scss" scoped>
.news-feed {
  overflow-y: auto;
  max-height: 100%;
}

.news-item {
  padding: 8px 12px;
  border-bottom: 1px solid $border-color;
  cursor: pointer;
  transition: background 0.15s;

  &:hover { background: rgba(255, 255, 255, 0.03); }

  &.high { border-left: 3px solid $color-up; background: $color-importance-high; }
  &.mid { border-left: 3px solid $color-flat; background: $color-importance-mid; }
  &.low { border-left: 3px solid transparent; background: $color-importance-low; }
}

.news-header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sim-icon { font-size: 14px; flex-shrink: 0; }

.news-title {
  flex: 1;
  font-size: 13px;
  color: $text-primary;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.news-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.news-source {
  font-size: 11px;
  color: $text-muted;
}

.news-time {
  font-size: 11px;
  color: $text-muted;
  font-family: 'Roboto Mono', monospace;
}

.importance-badge {
  display: inline-block;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  text-align: center;
  line-height: 20px;
  font-size: 11px;
  font-weight: 700;
  font-family: 'Roboto Mono', monospace;

  &.high { background: rgba($color-up, 0.2); color: $color-up; }
  &.mid { background: rgba($color-flat, 0.2); color: $color-flat; }
  &.low { background: rgba(128, 128, 128, 0.2); color: $text-muted; }
}

.news-body {
  margin-top: 8px;
  padding: 8px;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 4px;
  font-size: 12px;
  color: $text-secondary;
  line-height: 1.6;
}

.sim-summary {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed $border-color;
  color: $color-accent-light;
}

.news-empty {
  text-align: center;
  padding: 24px;
  color: $text-muted;
}
</style>
