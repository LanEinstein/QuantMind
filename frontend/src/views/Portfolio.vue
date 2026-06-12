<template>
  <div class="portfolio-layout">
    <!-- Section F: Multi-Account Tabs -->
    <AccountTabs />

    <!-- Circuit Breaker Alert -->
    <el-alert
      v-if="store.circuitBreakerStatus?.halted"
      title="交易已暂停 — 熔断器已触发"
      type="error"
      :closable="false"
      show-icon
      class="circuit-breaker-alert"
    >
      <template #default>
        日亏损: {{ ((store.circuitBreakerStatus?.daily_pnl_pct ?? 0) * 100).toFixed(2) }}%
        | 连续亏损: {{ store.circuitBreakerStatus?.consecutive_losses ?? 0 }} 笔
      </template>
    </el-alert>

    <!-- Section A: Account Overview Banner.
         Gate on BOTH stores so the banner does not render a stale
         simulation-only label while riskStore.fetchStatus is still in
         flight (codex review cycle 2 follow-up). -->
    <AccountBanner
      v-if="store.account && riskStore.riskStatus"
      :account="store.account"
    />

    <!-- Section B: Position Table -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">持仓明细</span>
          <el-tag v-if="store.positions.length > 0" size="small" type="info" effect="plain">
            {{ store.positions.length }} 只
          </el-tag>
          <!-- AD-005 — manual-trade entry, only in feishu_interactive mode -->
          <el-button
            v-if="feishuMode"
            size="small"
            type="primary"
            plain
            class="manual-trade-btn"
            @click="openManualTrade(null)"
          >
            记录手动操作
          </el-button>
        </div>
      </template>
      <PositionTable
        :positions="store.positions"
        :total-assets="store.account?.total_assets ?? 0"
        :can-record="feishuMode"
        @select-position="onSelectPosition"
        @record-manual="onRecordManual"
      />
    </el-card>

    <!-- Section B.5: EquityPoint MTM snapshot (G-004 read-only) -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">EquityPoint MTM 快照</span>
          <el-tag v-if="equityPoint" size="small" :type="equityQualityType">
            {{ equityPoint.quality }}
          </el-tag>
          <span v-if="equityPoint" class="card-subtitle">
            @{{ formatTime(equityPoint.snapshot_at) }}
          </span>
        </div>
      </template>
      <div v-if="equityRepoStatus === 'unavailable'" class="placeholder-text">
        MTM 仓库未接线(BrokerScheduler.intraday_mtm cron 上线后启用)。
      </div>
      <div v-else-if="!equityPoint" class="placeholder-text">
        尚未生成 EquityPoint(等待首个 30s MTM tick)。
      </div>
      <div v-else class="mtm-grid">
        <div class="mtm-row">
          <span>总权益</span><span>{{ equityPoint.total_equity.toFixed(2) }}</span>
        </div>
        <div class="mtm-row">
          <span>现金 / 冻结</span>
          <span>{{ equityPoint.cash.toFixed(2) }} / {{ equityPoint.frozen_cash.toFixed(2) }}</span>
        </div>
        <div class="mtm-row">
          <span>持仓市值</span><span>{{ equityPoint.market_value.toFixed(2) }}</span>
        </div>
        <div class="mtm-row">
          <span>累计 PnL</span>
          <span :class="equityPoint.pnl >= 0 ? 'text-up' : 'text-down'">
            {{ equityPoint.pnl.toFixed(2) }} ({{ (equityPoint.pnl_pct * 100).toFixed(2) }}%)
          </span>
        </div>
        <el-table
          :data="mtmPositions"
          stripe
          size="small"
          class="mtm-table"
          :show-overflow-tooltip="true"
        >
          <el-table-column prop="code" label="代码" width="90" />
          <el-table-column prop="volume" label="数量" width="80" align="right" />
          <el-table-column prop="cost_price" label="成本价" width="90" align="right">
            <template #default="{ row }">{{ row.cost_price.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="last_price" label="现价(price_source)" width="180" align="right">
            <template #default="{ row }">
              {{ row.last_price.toFixed(2) }}
              <el-tag
                size="small"
                :type="qualityToTag(row.price_quality)"
                effect="plain"
              >
                {{ row.price_quality }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_price_at" label="last_price_at(staleness)" width="220">
            <template #default="{ row }">
              <span v-if="row.last_price_at">
                {{ formatTime(row.last_price_at) }}
                <span class="staleness-hint">·{{ stalenessHint(row.last_price_at) }}</span>
              </span>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="market_value" label="市值" align="right">
            <template #default="{ row }">{{ row.market_value.toFixed(2) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <!-- Section C + D: Orders & Trades (read-only per P1-5 §2) -->
    <el-card shadow="never" class="section-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="今日委托" name="orders">
          <OrderList :orders="store.orders" />
        </el-tab-pane>
        <el-tab-pane label="成交历史" name="trades">
          <TradeHistory />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- Z-003 持仓 thesis 追踪(长持 vs 止盈)-->
    <ThesisTrackingPanel />

    <!-- Z-004 5 槽组合 + 换仓视图 -->
    <SlotRotationPanel :held-count="store.positions.length" />

    <!-- Loading overlay -->
    <div v-if="store.status === 'loading'" class="loading-overlay">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>

    <!-- Position Detail Drawer -->
    <PositionDetailDrawer
      v-model="showPositionDrawer"
      :position="selectedPosition"
    />

    <!-- AD-005 — manual-trade record form (feishu_interactive only) -->
    <ManualTradeForm
      v-model:visible="showManualTrade"
      :prefill="manualPrefill"
      @recorded="onManualRecorded"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { usePortfolioStore } from '@/stores/portfolio'
import { useRiskStore } from '@/stores/risk'
import { useWebSocket } from '@/composables/useWebSocket'
import { equityPointsApi } from '@/api/equityPoints'
import type { PositionItem } from '@/types/trading'
import type {
  EquityPointSnapshot,
  EquityPointQuality,
} from '@/types/equityPoint'
import AccountTabs from '@/components/trading/AccountTabs.vue'
import AccountBanner from '@/components/trading/AccountBanner.vue'
import PositionTable from '@/components/trading/PositionTable.vue'
import OrderList from '@/components/trading/OrderList.vue'
import TradeHistory from '@/components/trading/TradeHistory.vue'
import PositionDetailDrawer from '@/components/trading/PositionDetailDrawer.vue'
import ThesisTrackingPanel from '@/components/portfolio/ThesisTrackingPanel.vue'
import SlotRotationPanel from '@/components/portfolio/SlotRotationPanel.vue'
import ManualTradeForm from '@/components/trading/ManualTradeForm.vue'

const store = usePortfolioStore()
const riskStore = useRiskStore()
const { connect: connectWs } = useWebSocket()
const activeTab = ref('orders')
const showPositionDrawer = ref(false)
const selectedPosition = ref<PositionItem | null>(null)

// AD-005 — manual-trade entry is only available in feishu_interactive mode
// (pure simulation_auto is fully automated; the endpoint also 403s).
const feishuMode = computed(
  () => riskStore.riskStatus?.run_mode?.feishu_interactive ?? false,
)
const showManualTrade = ref(false)
const manualPrefill = ref<{
  code?: string
  side?: 'BUY' | 'SELL'
  sellableVolume?: number | null
}>({})

function openManualTrade(
  prefill: { code?: string; side?: 'BUY' | 'SELL'; sellableVolume?: number | null } | null,
): void {
  manualPrefill.value = prefill ?? {}
  showManualTrade.value = true
}

function onRecordManual(position: PositionItem): void {
  openManualTrade({
    code: position.code,
    side: 'SELL',
    sellableVolume: position.available_volume,
  })
}

async function onManualRecorded(): Promise<void> {
  // A manual BUY/SELL changes cash + account totals AND appends a trade, so
  // refresh the full account/positions/orders/trades set (not just positions)
  // plus the equity snapshot (codex P2).
  await Promise.allSettled([store.fetchAll(), refreshEquityPoint()])
}

const equityPoint = ref<EquityPointSnapshot | null>(null)
const equityRepoStatus = ref<'ok' | 'unavailable'>('unavailable')

const equityQualityType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  if (!equityPoint.value) return 'info'
  return qualityToTag(equityPoint.value.quality)
})

const mtmPositions = computed(() =>
  equityPoint.value ? [...equityPoint.value.positions] : [],
)

let refreshTimer: ReturnType<typeof setInterval> | null = null
let equityTimer: ReturnType<typeof setInterval> | null = null

function onSelectPosition(position: PositionItem) {
  selectedPosition.value = position
  showPositionDrawer.value = true
}

async function refreshEquityPoint(): Promise<void> {
  try {
    const payload = await equityPointsApi.getLatest()
    equityPoint.value = payload.point
    equityRepoStatus.value = payload.repository_status
  } catch {
    // Silent fail-open; the empty-state placeholder covers the user message.
    equityRepoStatus.value = 'unavailable'
  }
}

function qualityToTag(
  quality: EquityPointQuality,
): 'success' | 'warning' | 'danger' | 'info' {
  if (quality === 'FRESH') return 'success'
  if (quality === 'STALE') return 'warning'
  if (quality === 'DEGRADED') return 'danger'
  return 'info'
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

function stalenessHint(iso: string): string {
  try {
    const ageMs = Date.now() - new Date(iso).getTime()
    if (ageMs < 0) return '<0s'
    if (ageMs < 60_000) return `${Math.floor(ageMs / 1000)}s 前`
    if (ageMs < 3_600_000) return `${Math.floor(ageMs / 60_000)}m 前`
    return `${Math.floor(ageMs / 3_600_000)}h 前`
  } catch {
    return '—'
  }
}

onMounted(async () => {
  connectWs()
  // AccountBanner reads riskStore.riskStatus.run_mode to render the
  // simulation/Feishu tag. If the operator lands on /portfolio without
  // first visiting /risk, the store is empty and the banner mis-renders
  // simulation-only even when FEISHU_INTERACTIVE_ENABLED=true. Load
  // both stores in parallel here so the banner always sees the truth.
  await Promise.allSettled([
    store.fetchAll(),
    riskStore.fetchStatus(),
    refreshEquityPoint(),
  ])
  refreshTimer = setInterval(async () => {
    await Promise.allSettled([store.fetchPositions(), store.fetchOrders()])
  }, 30_000)
  equityTimer = setInterval(() => {
    void refreshEquityPoint()
  }, 30_000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
  if (equityTimer) {
    clearInterval(equityTimer)
    equityTimer = null
  }
})
</script>

<style scoped lang="scss">
.portfolio-layout {
  padding: $gap-md;
  position: relative;
  min-height: 100%;
}

.circuit-breaker-alert {
  margin-bottom: $gap-md;
}

.section-card {
  margin-bottom: $gap-md;
  background: $bg-card;
  border-color: $border-color;
}

.card-header {
  display: flex;
  align-items: center;
  gap: $gap-sm;
}

.card-title {
  font-weight: 600;
  color: $text-primary;
}

.manual-trade-btn {
  margin-left: auto;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba($bg-primary, 0.6);
  z-index: 10;
  border-radius: $border-radius;
}

.card-subtitle {
  font-size: 11px;
  color: $text-muted;
  margin-left: $gap-sm;
  font-family: 'Roboto Mono', monospace;
}

.mtm-grid {
  display: flex;
  flex-direction: column;
  gap: $gap-sm;
}
.mtm-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: $text-secondary;
  span:last-child {
    color: $text-primary;
    font-family: 'Roboto Mono', monospace;
  }
}
.mtm-table {
  margin-top: $gap-sm;
}

.staleness-hint {
  margin-left: 6px;
  color: $text-muted;
  font-size: 11px;
}

.text-muted { color: $text-muted; }
.placeholder-text {
  font-size: 12px;
  color: $text-muted;
  padding: $gap-sm 0;
}
</style>
