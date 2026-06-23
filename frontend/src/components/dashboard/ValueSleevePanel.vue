<template>
  <el-card shadow="never" class="value-sleeve-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">🏛 价值仓监控(长线埋伏)</span>
        <el-button text size="small" :loading="loading" @click="fetchTheses">
          刷新
        </el-button>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <div v-else-if="!payload" class="placeholder-text">加载中…</div>
    <div v-else-if="!payload.available" class="placeholder-text">
      {{ payload.note || '价值仓 thesis 未接线(系统停机 / 尚无价值持仓)。' }}
    </div>
    <div v-else-if="payload.theses.length === 0" class="placeholder-text">
      暂无价值仓持仓(总权益达 ¥5 万触发价值子账户后,埋伏名在此显示)。
    </div>
    <div v-else class="value-list">
      <article v-for="t in payload.theses" :key="t.stock_code" class="value-card">
        <header class="value-head">
          <span class="value-name">{{ t.stock_name }}（{{ t.stock_code }}）</span>
          <span class="value-badge">🏛 价值</span>
        </header>
        <dl class="value-kv">
          <dt>入场价</dt>
          <dd>{{ t.entry_price }}</dd>
          <dt>入场分</dt>
          <dd>{{ t.entry_score.toFixed(2) }}</dd>
          <dt>时间止损(交易日)</dt>
          <dd>{{ t.time_stop_trade_days }}</dd>
          <dt>建仓日</dt>
          <dd>{{ t.trade_date }}</dd>
        </dl>
        <div v-if="t.pillars.length" class="value-pillars">
          <span class="value-sub">支柱(LLM 复盘,display-only)</span>
          <ul>
            <li v-for="(p, i) in t.pillars" :key="i">{{ p }}</li>
          </ul>
        </div>
        <div v-if="t.invalidation_conditions.length" class="value-conds">
          <span class="value-sub">失效阈值(确定性量化)</span>
          <ul>
            <li v-for="(c, i) in t.invalidation_conditions" :key="i">
              {{ templateLabel(c.template) }}:{{ c.metric_name }}
              {{ c.comparator }} {{ c.threshold }}
            </li>
          </ul>
        </div>
      </article>
    </div>

    <p v-if="payload" class="value-foot">
      <span class="value-count">价值仓持仓 {{ payload.theses.length }} 只(上限 3)</span>
      <span class="value-note">{{ payload.advisory.note }}</span>
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { positionThesesApi } from '@/api/positionTheses'
import {
  INVALIDATION_TEMPLATE_LABELS,
  type PositionThesesPayload,
} from '@/types/positionThesis'

// Slow poll: the value sleeve is a long-term 埋伏 hold, so a 60s refresh keeps
// the read-only view current without a WS class (P1-5-amendment §1.3 polling).
const POLL_INTERVAL_MS = 60_000

const payload = ref<PositionThesesPayload | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function templateLabel(template: string): string {
  return INVALIDATION_TEMPLATE_LABELS[template] ?? template
}

async function fetchTheses(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    payload.value = await positionThesesApi.list('value')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load value sleeve'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void fetchTheses()
  timer = setInterval(() => void fetchTheses(), POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
})

defineExpose({ fetchTheses })
</script>

<style lang="scss" scoped>
.value-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: $gap-md;
}
.value-card {
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 12px;
}
.value-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: $gap-sm;
}
.value-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}
.value-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
  background: rgba(64, 158, 255, 0.18);
  color: $text-primary;
}
.value-kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0 0 $gap-sm;
  dt {
    color: $text-muted;
    font-size: 12px;
  }
  dd {
    color: $text-primary;
    font-size: 12px;
    margin: 0;
  }
}
.value-sub {
  display: block;
  font-size: 11px;
  color: $text-muted;
  margin-top: 6px;
}
.value-pillars ul,
.value-conds ul {
  margin: 2px 0 0;
  padding-left: 18px;
  li {
    font-size: 12px;
    color: $text-primary;
    line-height: 1.5;
  }
}
.value-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: $gap-md 0 0;
}
.value-count {
  font-size: 12px;
  font-weight: 600;
  color: $text-primary;
}
.value-note {
  font-size: 11px;
  color: $text-muted;
  line-height: 1.5;
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
