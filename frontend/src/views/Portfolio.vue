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

    <!-- Section A: Account Overview Banner -->
    <AccountBanner v-if="store.account" :account="store.account" />

    <!-- Section B: Position Table -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">持仓明细</span>
          <el-tag v-if="store.positions.length > 0" size="small" type="info" effect="plain">
            {{ store.positions.length }} 只
          </el-tag>
        </div>
      </template>
      <PositionTable
        :positions="store.positions"
        :total-assets="store.account?.total_assets ?? 0"
        @select-position="onSelectPosition"
      />
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

    <!-- Loading overlay -->
    <div v-if="store.status === 'loading'" class="loading-overlay">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
    </div>

    <!-- Position Detail Drawer -->
    <PositionDetailDrawer
      v-model="showPositionDrawer"
      :position="selectedPosition"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { usePortfolioStore } from '@/stores/portfolio'
import { useWebSocket } from '@/composables/useWebSocket'
import type { PositionItem } from '@/types/trading'
import AccountTabs from '@/components/trading/AccountTabs.vue'
import AccountBanner from '@/components/trading/AccountBanner.vue'
import PositionTable from '@/components/trading/PositionTable.vue'
import OrderList from '@/components/trading/OrderList.vue'
import TradeHistory from '@/components/trading/TradeHistory.vue'
import PositionDetailDrawer from '@/components/trading/PositionDetailDrawer.vue'

const store = usePortfolioStore()
const { connect: connectWs } = useWebSocket()
const activeTab = ref('orders')
const showPositionDrawer = ref(false)
const selectedPosition = ref<PositionItem | null>(null)

let refreshTimer: ReturnType<typeof setInterval> | null = null

function onSelectPosition(position: PositionItem) {
  selectedPosition.value = position
  showPositionDrawer.value = true
}

onMounted(async () => {
  connectWs()
  await store.fetchAll()
  refreshTimer = setInterval(async () => {
    await Promise.allSettled([store.fetchPositions(), store.fetchOrders()])
  }, 30_000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
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
</style>
