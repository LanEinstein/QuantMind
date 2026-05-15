<template>
  <section class="system-status">
    <header class="page-header">
      <h2 class="page-title">系统状态 · 五独立冻结源</h2>
      <p class="page-subtitle">
        P1-5 §1.1 锁定:模式切换 / 对账冻结 / 熔断冷却 / 数据质量 / 日终管线五个独立冻结源,严禁聚合 frozen=true。
      </p>
    </header>

    <div v-if="store.error" class="error-banner">
      加载失败:{{ store.error }}
    </div>

    <div class="cards-grid">
      <article
        v-for="source in store.sources"
        :key="source.name"
        :class="['source-card', cardClass(source)]"
        data-testid="freeze-source-card"
      >
        <header class="card-header">
          <span class="card-title">{{ LABELS[source.name] }}</span>
          <span :class="['status-pill', cardClass(source)]" data-testid="status-pill">
            {{ pillText(source) }}
          </span>
        </header>

        <dl class="kv-list">
          <template v-for="row in describe(source)" :key="row.key">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </template>
        </dl>
      </article>
    </div>

    <footer class="page-footer">
      <span>最后更新:{{ formattedTimestamp }}</span>
      <el-button size="small" :loading="store.loading" @click="refresh">刷新</el-button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useSystemStatusStore } from '@/stores/systemStatus'
import {
  FREEZE_SOURCE_LABELS as LABELS,
  type FreezeSource,
} from '@/types/systemStatus'

const store = useSystemStatusStore()

const POLL_INTERVAL_MS = 10_000
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  store.fetchFreezeSources()
  pollTimer = setInterval(() => store.fetchFreezeSources(), POLL_INTERVAL_MS)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})

function refresh(): void {
  store.fetchFreezeSources()
}

function cardClass(source: FreezeSource): string {
  if (source.status === 'unavailable') return 'unavailable'
  return source.active ? 'active' : 'idle'
}

function pillText(source: FreezeSource): string {
  if (source.status === 'unavailable') return '探针未就绪'
  return source.active ? '冻结生效' : '正常'
}

interface KV {
  readonly key: string
  readonly label: string
  readonly value: string
}

function describe(source: FreezeSource): KV[] {
  const rows: KV[] = []
  const reasonValue = source.reason ?? '—'
  rows.push({ key: 'reason', label: '原因', value: reasonValue })

  switch (source.name) {
    case 'mode_switch': {
      const ctx = source.context
      rows.push({
        key: 'from_to',
        label: '切换方向',
        value: ctx ? `${ctx.from_mode ?? '—'} → ${ctx.to_mode ?? '—'}` : '—',
      })
      rows.push({
        key: 'started_at',
        label: '触发时间',
        value: ctx?.started_at ?? '—',
      })
      rows.push({
        key: 'initiated_by',
        label: '发起人',
        value: ctx?.initiated_by ?? '—',
      })
      break
    }
    case 'reconciliation_ticket':
      rows.push({ key: 'ticket', label: 'Ticket ID', value: source.ticket_id ?? '—' })
      break
    case 'circuit_breaker':
      rows.push({ key: 'halted_at', label: '熔断时间', value: source.halted_at ?? '—' })
      rows.push({
        key: 'losses',
        label: '连续亏损',
        value: source.consecutive_losses === null ? '—' : `${source.consecutive_losses}`,
      })
      break
    case 'data_quality':
      rows.push({ key: 'code', label: '触发标的', value: source.code ?? '—' })
      break
    case 'eod_pipeline':
      rows.push({ key: 'raised_at', label: '触发时间', value: source.raised_at ?? '—' })
      rows.push({ key: 'trade_date', label: '触发交易日', value: source.trade_date ?? '—' })
      break
  }

  return rows
}

const formattedTimestamp = computed(() => {
  if (!store.timestamp) return '—'
  try {
    return new Date(store.timestamp).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return store.timestamp
  }
})
</script>

<style lang="scss" scoped>
.system-status {
  padding: $gap-md $gap-lg;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: $gap-md;
  overflow-y: auto;
}

.page-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: $text-primary;
  margin: 0;
}
.page-subtitle {
  font-size: 12px;
  color: $text-muted;
  margin: 0;
}

.error-banner {
  padding: 8px 12px;
  background: rgba(255, 23, 68, 0.12);
  color: $color-down; // green=down per A-share theme; ignore — using red text
  border: 1px solid rgba(255, 23, 68, 0.3);
  border-radius: 6px;
  color: $status-red;
  font-size: 12px;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: $gap-md;
}

.source-card {
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.source-card.active {
  border-color: rgba(255, 23, 68, 0.45);
  box-shadow: 0 0 0 1px rgba(255, 23, 68, 0.15);
}

.source-card.unavailable {
  border-color: rgba(142, 142, 160, 0.35);
  opacity: 0.85;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.status-pill {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
}
.status-pill.idle {
  background: rgba(0, 200, 83, 0.15);
  color: $status-green;
}
.status-pill.active {
  background: rgba(255, 23, 68, 0.15);
  color: $status-red;
}
.status-pill.unavailable {
  background: rgba(142, 142, 160, 0.18);
  color: $text-muted;
}

.kv-list {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 4px 12px;
  margin: 0;

  dt {
    color: $text-muted;
    font-size: 12px;
  }
  dd {
    color: $text-primary;
    font-size: 12px;
    margin: 0;
    word-break: break-all;
  }
}

.page-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: $text-muted;
}
</style>
