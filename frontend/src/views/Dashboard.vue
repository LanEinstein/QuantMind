<template>
  <div class="dashboard-layout">
    <div class="dashboard-content">
      <!-- Zone A: Three Major Indices -->
      <el-row :gutter="12" class="zone-a">
        <el-col :span="8" v-for="idx in store.indices" :key="idx.code">
          <el-card shadow="never" class="index-card">
            <MarketChart
              :name="idx.name"
              :price="idx.price"
              :change-pct="idx.change_pct"
            />
          </el-card>
        </el-col>
      </el-row>

      <!-- Zones B+C: Market Stats (left) + Sector Heatmap (right) -->
      <el-row :gutter="12" class="zone-bc">
        <el-col :span="8">
          <el-card shadow="never" class="stats-card">
            <template #header>
              <span class="card-title">涨跌统计</span>
            </template>
            <!-- Rising / Falling bar -->
            <div class="stats-bar-container">
              <div class="stats-bar">
                <div class="bar-rise" :style="{ width: risePercent + '%' }">
                  {{ store.marketStats.rising }}
                </div>
                <div class="bar-flat" :style="{ width: flatPercent + '%' }">
                  {{ store.marketStats.flat }}
                </div>
                <div class="bar-fall" :style="{ width: fallPercent + '%' }">
                  {{ store.marketStats.falling }}
                </div>
              </div>
            </div>
            <div class="limit-row">
              <span class="limit-up">涨停 {{ store.marketStats.limit_up }}</span>
              <span class="limit-down">跌停 {{ store.marketStats.limit_down }}</span>
            </div>
          </el-card>
        </el-col>
        <el-col :span="16">
          <el-card shadow="never" class="heatmap-card">
            <template #header>
              <span class="card-title">板块热力图</span>
            </template>
            <SectorHeatmap :sectors="store.sectors" />
          </el-card>
        </el-col>
      </el-row>

      <!-- Zones D+E: Capital Flow (left) + News Feed (right) -->
      <el-row :gutter="12" class="zone-de">
        <el-col :span="8">
          <el-card shadow="never" class="flow-card">
            <template #header>
              <span class="card-title">北向资金</span>
            </template>
            <CapitalFlowChart
              :net-inflow="store.capitalFlow?.north_net_inflow ?? 0"
            />
          </el-card>
        </el-col>
        <el-col :span="16">
          <el-card shadow="never" class="news-card">
            <template #header>
              <span class="card-title">新闻快讯</span>
              <span class="card-subtitle">DeepSeek重要性评分</span>
            </template>
            <NewsFeed :articles="store.news" />
          </el-card>
        </el-col>
      </el-row>

      <!-- Zone F: Agent Decision Banner -->
      <div class="zone-f">
        <el-card shadow="never" class="agent-banner">
          <span class="agent-icon">🤖</span>
          <span class="agent-text">
            {{ store.latestSignal || '基金经理建议: 等待最新分析结果...' }}
          </span>
          <el-button text size="small" class="agent-link">
            查看详情 →
          </el-button>
        </el-card>
      </div>

      <!-- Z-005 双线每日并行运行态(编排概览;轮询,不扩 WS)-->
      <div class="zone-dual-line">
        <DualLineStatusPanel />
      </div>
    </div>
    <!-- G-002: status bar moved to AppShell.vue (global) -->
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useMarketStore } from '@/stores/market'
import { useWebSocket } from '@/composables/useWebSocket'
import MarketChart from '@/components/charts/MarketChart.vue'
import SectorHeatmap from '@/components/charts/SectorHeatmap.vue'
import CapitalFlowChart from '@/components/charts/CapitalFlowChart.vue'
import NewsFeed from '@/components/common/NewsFeed.vue'
import DualLineStatusPanel from '@/components/dashboard/DualLineStatusPanel.vue'

const store = useMarketStore()
const { connected: wsConnected, connect } = useWebSocket()
void wsConnected

// Market stats percentages
const total = computed(() => {
  const s = store.marketStats
  return s.rising + s.falling + s.flat || 1
})
const risePercent = computed(() => (store.marketStats.rising / total.value) * 100)
const flatPercent = computed(() => (store.marketStats.flat / total.value) * 100)
const fallPercent = computed(() => (store.marketStats.falling / total.value) * 100)

onMounted(async () => {
  await store.fetchAll()
  // Only auto-connect WebSocket when VITE_WS_URL is configured or backend
  // exposes the /ws/market endpoint. Without this guard the composable enters
  // an infinite reconnect loop against a non-existent endpoint.
  if (import.meta.env.VITE_WS_URL) {
    connect()
  }
})
</script>

<style lang="scss" scoped>
.dashboard-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: $bg-primary;
}

.dashboard-content {
  flex: 1;
  overflow-y: auto;
  padding: $gap-md;
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

.card-subtitle {
  font-size: 11px;
  color: $text-muted;
  margin-left: 8px;
}

// Zone A: Index charts
.zone-a {
  .index-card {
    height: 240px;
    .el-card__body { height: calc(100% - 44px); padding: 8px 12px; }
  }
}

// Zone BC
.zone-bc {
  .stats-card {
    height: 280px;
  }
  .heatmap-card {
    height: 280px;
    .el-card__body { height: calc(100% - 44px); }
  }
}

// Stats bar
.stats-bar-container {
  margin: 16px 0;
}

.stats-bar {
  display: flex;
  height: 28px;
  border-radius: 4px;
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
  line-height: 28px;
  text-align: center;
  color: #fff;
}

.bar-rise { background: $color-up; min-width: 20px; }
.bar-flat { background: #616161; min-width: 20px; }
.bar-fall { background: $color-down; min-width: 20px; }

.limit-row {
  display: flex;
  justify-content: space-between;
  margin-top: 12px;
  font-size: 14px;
  font-weight: 700;
}

.limit-up { color: $color-up; }
.limit-down { color: $color-down; }

// Zone DE
.zone-de {
  .flow-card {
    height: 280px;
    .el-card__body { height: calc(100% - 44px); }
  }
  .news-card {
    height: 280px;
    .el-card__header {
      display: flex;
      align-items: baseline;
    }
    .el-card__body { height: calc(100% - 44px); padding: 0; overflow: hidden; }
  }
}

// Zone F: Agent banner
.zone-f {
  .agent-banner {
    .el-card__body {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
    }
  }
}

.agent-icon { font-size: 18px; }
.agent-text {
  flex: 1;
  font-size: 13px;
  color: $text-primary;
}

.agent-link {
  color: $color-accent !important;
  font-size: 12px;
}
</style>
