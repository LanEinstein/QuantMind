<template>
  <div class="trade-history">
    <div class="filter-bar">
      <el-input
        v-model="store.tradeFilterCode"
        placeholder="股票代码"
        clearable
        size="small"
        class="filter-code"
      />
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        size="small"
        value-format="YYYY-MM-DD"
        class="filter-date"
        @change="onDateChange"
      />
      <el-button size="small" type="primary" :icon="Download" @click="store.exportTradesCSV()">
        导出CSV
      </el-button>
    </div>

    <el-table :data="store.filteredTrades" stripe size="small" class="dark-table">
      <el-table-column prop="traded_at" label="时间" width="170">
        <template #default="{ row }">
          {{ formatTime(row.traded_at) }}
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
      <el-table-column prop="direction" label="方向" width="70" align="center">
        <template #default="{ row }">
          <span :class="row.direction === 'BUY' ? 'text-up' : 'text-down'">
            {{ row.direction === 'BUY' ? '买入' : '卖出' }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="price" label="价格" width="90" align="right">
        <template #default="{ row }">
          {{ num(row.price).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column prop="volume" label="数量" width="80" align="right" />
      <el-table-column prop="amount" label="金额" width="120" align="right">
        <template #default="{ row }">
          {{ num(row.amount).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column prop="commission" label="佣金" width="80" align="right">
        <template #default="{ row }">
          {{ num(row.commission).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column prop="stamp_tax" label="印花税" width="80" align="right">
        <template #default="{ row }">
          {{ num(row.stamp_tax).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column
        prop="transfer_fee"
        label="过户费(深 0.00341%)"
        width="170"
        align="right"
      >
        <template #default="{ row }">
          {{ num(row.transfer_fee).toFixed(4) }}
        </template>
      </el-table-column>
      <el-table-column prop="slippage_cost" label="滑点" width="80" align="right">
        <template #default="{ row }">
          {{ num(row.slippage_cost).toFixed(2) }}
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无成交记录" :image-size="60" />
      </template>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { Download } from '@element-plus/icons-vue'
import { usePortfolioStore, getStockName } from '@/stores/portfolio'
import { num } from '@/utils/num'
import dayjs from 'dayjs'

const store = usePortfolioStore()
const dateRange = ref<[string, string] | null>(null)

function onDateChange(val: [string, string] | null) {
  store.tradeFilterDateRange = val
}

function formatTime(iso: string): string {
  return dayjs(iso).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style scoped lang="scss">
.filter-bar {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  margin-bottom: $gap-md;
}

.filter-code {
  width: 120px;
}

.filter-date {
  width: 260px;
}

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
