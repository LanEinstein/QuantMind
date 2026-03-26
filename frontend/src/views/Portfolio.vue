<template>
  <div class="portfolio-layout">
    <!-- Section F: Multi-Account Tabs -->
    <AccountTabs />

    <!-- Section E: Approval Queue (conditional) -->
    <ApprovalQueue
      v-if="store.hasPendingApprovals"
      :approvals="store.pendingApprovals"
      @approve="onApprove"
      @reject="onReject"
    />

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
      />
    </el-card>

    <!-- Section C + D: Orders & Trades -->
    <el-card shadow="never" class="section-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="今日委托" name="orders">
          <OrderList :orders="store.orders" @cancel="onCancelOrder" />
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
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePortfolioStore } from '@/stores/portfolio'
import AccountTabs from '@/components/trading/AccountTabs.vue'
import AccountBanner from '@/components/trading/AccountBanner.vue'
import PositionTable from '@/components/trading/PositionTable.vue'
import OrderList from '@/components/trading/OrderList.vue'
import TradeHistory from '@/components/trading/TradeHistory.vue'
import ApprovalQueue from '@/components/trading/ApprovalQueue.vue'

const store = usePortfolioStore()
const activeTab = ref('orders')

let refreshTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  await store.fetchAll()
  // Refresh positions and orders every 10 seconds
  refreshTimer = setInterval(async () => {
    await Promise.allSettled([store.fetchPositions(), store.fetchOrders()])
  }, 10_000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})

async function onCancelOrder(orderId: string) {
  try {
    await store.cancelOrder(orderId)
    ElMessage.success('撤单成功')
  } catch {
    ElMessage.error('撤单失败')
  }
}

async function onApprove(id: string) {
  try {
    await store.approveOrder(id)
    ElMessage.success('订单已批准')
  } catch {
    ElMessage.error('批准失败')
  }
}

async function onReject(id: string) {
  try {
    await store.rejectOrder(id)
    ElMessage.success('订单已拒绝')
  } catch {
    ElMessage.error('拒绝失败')
  }
}
</script>

<style scoped lang="scss">
.portfolio-layout {
  padding: $gap-md;
  position: relative;
  min-height: 100%;
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
