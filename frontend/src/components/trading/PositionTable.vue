<template>
  <div class="position-table">
    <el-table
      :data="[...positions]"
      stripe
      size="small"
      class="dark-table"
      :row-class-name="rowClassName"
    >
      <el-table-column prop="code" label="代码" width="90">
        <template #default="{ row }">
          <span class="code-link">{{ row.code }}</span>
        </template>
      </el-table-column>
      <el-table-column label="名称" width="100">
        <template #default="{ row }">
          {{ getStockName(row.code) }}
        </template>
      </el-table-column>
      <el-table-column prop="volume" label="持仓" width="80" align="right" />
      <el-table-column prop="cost_price" label="成本" width="90" align="right">
        <template #default="{ row }">
          {{ row.cost_price.toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column label="现价" width="90" align="right">
        <template #default="{ row }">
          {{ currentPrice(row).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column label="盈亏" width="100" align="right">
        <template #default="{ row }">
          <span :class="pnlClass(row.unrealized_pnl)">
            {{ formatPnl(row.unrealized_pnl) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="盈亏%" width="80" align="right">
        <template #default="{ row }">
          <span :class="pnlClass(row.unrealized_pnl_pct)">
            {{ (row.unrealized_pnl_pct * 100).toFixed(2) }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column label="仓位占比" width="110">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round(row.position_pct * 100)"
            :stroke-width="14"
            :text-inside="true"
            :color="positionColor(row.position_pct)"
          />
        </template>
      </el-table-column>
      <el-table-column label="止损线" width="90" align="right">
        <template #default="{ row }">
          {{ row.stop_loss_line.toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column label="止损距离" width="90" align="right">
        <template #default="{ row }">
          <span :class="distanceClass(row.stop_loss_distance)">
            {{ (row.stop_loss_distance * 100).toFixed(1) }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column label="风控状态" width="110" align="center">
        <template #default="{ row }">
          <el-tag :type="riskTagType(row.risk_status)" size="small" effect="dark">
            {{ riskLabel(row.risk_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无持仓" :image-size="60" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import type { PositionItem, RiskStatusLevel } from '@/types/trading'
import { getStockName } from '@/stores/portfolio'

defineProps<{
  positions: readonly PositionItem[]
  totalAssets: number
}>()

function currentPrice(row: PositionItem): number {
  return row.volume > 0 ? row.market_value / row.volume : row.cost_price
}

function pnlClass(value: number): string {
  if (value > 0) return 'text-up'
  if (value < 0) return 'text-down'
  return ''
}

function formatPnl(value: number): string {
  const prefix = value > 0 ? '+' : ''
  return prefix + value.toFixed(2)
}

function distanceClass(distance: number): string {
  if (distance > 0.05) return 'distance-safe'
  if (distance >= 0.02) return 'distance-warn'
  return 'distance-danger'
}

function positionColor(pct: number): string {
  if (pct > 0.20) return '#ff1744'
  if (pct > 0.15) return '#ffd600'
  return '#448aff'
}

function riskTagType(status: RiskStatusLevel): 'success' | 'warning' | 'danger' {
  const map: Record<RiskStatusLevel, 'success' | 'warning' | 'danger'> = {
    normal: 'success',
    near_stop: 'warning',
    triggered: 'danger',
    over_limit: 'warning',
  }
  return map[status]
}

function riskLabel(status: RiskStatusLevel): string {
  const map: Record<RiskStatusLevel, string> = {
    normal: '正常',
    near_stop: '接近止损',
    triggered: '已触发',
    over_limit: '仓位超限',
  }
  return map[status]
}

function rowClassName({ row }: { row: PositionItem }): string {
  if (row.risk_status === 'triggered') return 'row-danger'
  if (row.risk_status === 'over_limit') return 'row-warning'
  return ''
}
</script>

<style scoped lang="scss">
.code-link {
  color: $color-accent;
  cursor: pointer;
}

.text-up {
  color: $color-up;
  font-weight: 600;
}

.text-down {
  color: $color-down;
  font-weight: 600;
}

.distance-safe {
  color: $status-green;
  font-weight: 600;
}

.distance-warn {
  color: $status-yellow;
  font-weight: 600;
}

.distance-danger {
  color: $status-red;
  font-weight: 600;
}

:deep(.row-danger) {
  background-color: rgba($status-red, 0.06) !important;
}

:deep(.row-warning) {
  background-color: rgba($status-yellow, 0.06) !important;
}
</style>
