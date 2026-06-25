<template>
  <el-card shadow="never" class="section-card rotation-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">5 槽组合 + 换仓</span>
        <el-tag
          v-if="payload?.available"
          size="small"
          :type="underinvested ? 'warning' : 'info'"
          effect="plain"
        >
          {{ slotLabel }}
        </el-tag>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <div v-else-if="!payload || !payload.available" class="placeholder-text">
      {{ payload?.note || '槽位轮动 runner 未接线(系统停机 / 轮动未启用)。' }}
    </div>
    <div v-else class="rotation-body">
      <div class="slot-strip" role="img" :aria-label="slotLabel">
        <span
          v-for="i in (payload.max_total_positions ?? 0)"
          :key="i"
          :class="['slot-cell', i <= heldCount ? 'filled' : 'empty']"
        />
        <span class="slot-strip-label">{{ slotLabel }}</span>
      </div>

      <div v-if="underinvested" class="banner banner-warn">
        欠配阻断激活(UNDERINVESTED_ROTATION_EXPIRED):卖后未补回,轮动暂停至人工 gate。
      </div>

      <h5 class="rotation-subtitle">在途换仓意图(卖谁 → 换谁)</h5>
      <el-table
        :data="[...payload.open_intents]"
        size="small"
        stripe
        empty-text="暂无在途换仓"
      >
        <el-table-column label="卖出(在位)" min-width="150">
          <template #default="{ row }">
            {{ row.incumbent_code }}
            <span class="pct">P{{ pct(row.incumbent_percentile) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="买入(挑战)" min-width="150">
          <template #default="{ row }">
            {{ row.challenger_code }}
            <span class="pct">P{{ pct(row.challenger_percentile) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_trade_date" label="发起日" width="100" />
        <el-table-column prop="expires_at_trade_date" label="到期日" width="100" />
      </el-table>

      <h5 class="rotation-subtitle">近期轮动事件(T+1 跨日态)</h5>
      <el-table
        :data="[...payload.recent_events]"
        size="small"
        stripe
        max-height="240"
        empty-text="暂无轮动事件"
      >
        <el-table-column label="事件" width="130">
          <template #default="{ row }">{{ eventLabel(row.event_type) }}</template>
        </el-table-column>
        <el-table-column prop="trade_date" label="交易日" width="100" />
        <el-table-column label="标的" min-width="140">
          <template #default="{ row }">
            <span v-if="row.incumbent_code">{{ row.incumbent_code }}→{{ row.challenger_code }}</span>
            <span v-else-if="row.buy_code">买回 {{ row.buy_code }}</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="说明" min-width="160" />
      </el-table>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { slotRotationApi } from '@/api/slotRotation'
import {
  ROTATION_EVENT_LABELS,
  type SlotRotationPayload,
} from '@/types/slotRotation'

const props = withDefaults(defineProps<{ heldCount?: number }>(), {
  heldCount: 0,
})

const payload = ref<SlotRotationPayload | null>(null)
const error = ref<string | null>(null)

const underinvested = computed(
  () => payload.value?.underinvested_block_active ?? false,
)
const slotLabel = computed(() => {
  const cap = payload.value?.max_total_positions ?? '—'
  return `${props.heldCount}/${cap} 槽占用`
})

function pct(value: number): string {
  return Math.round(value * 100).toString()
}

function eventLabel(eventType: string): string {
  return ROTATION_EVENT_LABELS[eventType] ?? eventType
}

async function fetchRotation(): Promise<void> {
  error.value = null
  try {
    payload.value = await slotRotationApi.get()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load rotation'
  }
}

// F5 (production-hardening 2026-06-25): poll like ValueSleevePanel so the panel
// stays live on a days-open dashboard instead of freezing on its mount snapshot.
const POLL_INTERVAL_MS = 60_000
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void fetchRotation()
  pollTimer = setInterval(() => void fetchRotation(), POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

defineExpose({ fetchRotation })
</script>

<style lang="scss" scoped>
.rotation-body {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}
.slot-strip {
  display: flex;
  align-items: center;
  gap: 6px;
}
.slot-cell {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  border: 1px solid $border-color;
}
.slot-cell.filled {
  background: $color-accent;
  border-color: $color-accent;
}
.slot-cell.empty {
  background: transparent;
}
.slot-strip-label {
  margin-left: 8px;
  font-size: 12px;
  color: $text-secondary;
}
.rotation-subtitle {
  font-size: 12px;
  font-weight: 600;
  color: $text-secondary;
  margin: 0;
}
.pct {
  font-size: 11px;
  color: $text-muted;
  margin-left: 4px;
}
.banner {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  border: 1px solid transparent;
}
.banner-warn {
  background: rgba(255, 183, 77, 0.12);
  border-color: rgba(255, 183, 77, 0.4);
  color: #ffb74d;
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
