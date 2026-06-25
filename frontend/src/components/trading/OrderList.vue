<template>
  <div class="order-list">
    <el-table :data="[...orders]" stripe size="small" class="dark-table">
      <el-table-column prop="created_at" label="时间" width="100">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column prop="direction" label="方向" width="70" align="center">
        <template #default="{ row }">
          <span :class="row.direction === 'BUY' ? 'text-up' : 'text-down'">
            {{ row.direction === 'BUY' ? '买' : '卖' }}
          </span>
        </template>
      </el-table-column>
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
      <el-table-column prop="price" label="价格" width="90" align="right">
        <template #default="{ row }">
          {{ num(row.price).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column prop="volume" label="数量" width="80" align="right" />
      <el-table-column prop="status" label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" effect="dark">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无委托" :image-size="60" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import type { OrderItem, OrderStatusType } from '@/types/trading'
import { getStockName } from '@/stores/portfolio'
import { num } from '@/utils/num'
import dayjs from 'dayjs'

defineProps<{
  orders: readonly OrderItem[]
}>()

function statusTagType(status: OrderStatusType): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  const map: Record<OrderStatusType, 'primary' | 'success' | 'warning' | 'info' | 'danger'> = {
    PENDING: 'primary',
    FILLED: 'success',
    CANCELLED: 'info',
    REJECTED: 'danger',
  }
  return map[status]
}

function statusLabel(status: OrderStatusType): string {
  const map: Record<OrderStatusType, string> = {
    PENDING: '待成交',
    FILLED: '已成交',
    CANCELLED: '已撤销',
    REJECTED: '已拒绝',
  }
  return map[status]
}

function formatTime(iso: string): string {
  return dayjs(iso).format('HH:mm:ss')
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
</style>
