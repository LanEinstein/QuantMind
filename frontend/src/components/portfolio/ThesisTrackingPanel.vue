<template>
  <el-card shadow="never" class="section-card thesis-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">持仓 thesis 追踪(长持 vs 止盈)</span>
        <el-tag size="small" type="info" effect="plain">
          {{ payload?.thesis_count ?? 0 }} 条
        </el-tag>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">
      加载失败:{{ error }}
    </div>
    <div v-else-if="!payload || !payload.available" class="placeholder-text">
      {{ payload?.note || '持仓 thesis 存储未接线(系统停机 / 尚未落 thesis)。' }}
    </div>
    <div v-else-if="payload.theses.length === 0" class="placeholder-text">
      暂无在持 thesis(买入时由 Line-1 落库;长线看支柱健康,短线看量化指标)。
    </div>
    <div v-else class="thesis-list">
      <article v-for="t in payload.theses" :key="t.stock_code" class="thesis-card">
        <header class="thesis-card-head">
          <span class="thesis-code">
            {{ t.stock_code }} {{ t.stock_name }}
            <el-tag
              v-if="styleBadge(t.style)"
              :type="styleBadge(t.style)!.tagType"
              size="small"
              effect="plain"
              class="thesis-style-badge"
            >{{ styleBadge(t.style)!.icon }}{{ styleBadge(t.style)!.label }}</el-tag>
          </span>
          <span class="thesis-meta">
            入场价 {{ t.entry_price.toFixed(2) }} · 入场分 {{ t.entry_score.toFixed(3) }}
            · 时间止损 {{ t.time_stop_trade_days }} 交易日
          </span>
        </header>

        <div class="thesis-section">
          <h5 class="thesis-subtitle">买入逻辑支柱(LLM advisory)</h5>
          <ul class="pillar-list">
            <li v-for="(p, i) in t.pillars" :key="i">{{ p }}</li>
          </ul>
        </div>

        <div class="thesis-section">
          <h5 class="thesis-subtitle">量化失效阈值(确定性,机检)</h5>
          <el-table :data="[...t.invalidation_conditions]" size="small" stripe>
            <el-table-column label="模板" min-width="100">
              <template #default="{ row }">{{ templateLabel(row.template) }}</template>
            </el-table-column>
            <el-table-column prop="metric_name" label="指标" width="90" />
            <el-table-column label="比较" width="70">
              <template #default="{ row }">{{ comparatorLabel(row.comparator) }}</template>
            </el-table-column>
            <el-table-column label="阈值" width="100">
              <template #default="{ row }">{{ row.threshold.toFixed(3) }}</template>
            </el-table-column>
            <el-table-column label="锚" width="100">
              <template #default="{ row }">{{ row.anchor.toFixed(3) }}</template>
            </el-table-column>
          </el-table>
        </div>

        <p v-if="t.catalyst_window_end" class="thesis-catalyst">
          催化窗口至:{{ t.catalyst_window_end }}
        </p>
      </article>
      <p class="advisory-note">{{ payload.advisory.note }}</p>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { positionThesesApi } from '@/api/positionTheses'
import {
  INVALIDATION_TEMPLATE_LABELS,
  type PositionThesesPayload,
} from '@/types/positionThesis'
import { styleBadge } from '@/utils/styleBadge'

const payload = ref<PositionThesesPayload | null>(null)
const error = ref<string | null>(null)

function templateLabel(template: string): string {
  return INVALIDATION_TEMPLATE_LABELS[template] ?? template
}

function comparatorLabel(comparator: string): string {
  if (comparator === 'lt') return '<'
  if (comparator === 'gt') return '>'
  return comparator
}

async function fetchTheses(): Promise<void> {
  error.value = null
  try {
    payload.value = await positionThesesApi.list()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load theses'
  }
}

onMounted(fetchTheses)

defineExpose({ fetchTheses })
</script>

<style lang="scss" scoped>
.thesis-list {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}
.thesis-card {
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 12px;
}
.thesis-card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: $gap-sm;
}
.thesis-code {
  font-weight: 600;
  color: $text-primary;
  font-size: 14px;
}
.thesis-meta {
  font-size: 11px;
  color: $text-muted;
}
.thesis-section {
  margin-top: $gap-sm;
}
.thesis-subtitle {
  font-size: 12px;
  font-weight: 600;
  color: $text-secondary;
  margin: 0 0 6px;
}
.pillar-list {
  margin: 0;
  padding-left: 18px;
  li {
    font-size: 12px;
    color: $text-primary;
    line-height: 1.6;
  }
}
.thesis-catalyst {
  font-size: 11px;
  color: $text-muted;
  margin: $gap-sm 0 0;
}
.advisory-note {
  font-size: 11px;
  color: $text-muted;
  line-height: 1.5;
  margin: 0;
}
.placeholder-text {
  padding: 24px;
  text-align: center;
  color: $text-muted;
  font-size: 13px;
}
.error-text {
  color: $status-red;
}
</style>
