<template>
  <section class="account-lines">
    <header class="page-header">
      <div>
        <h2 class="page-title">分线账本(只读)</h2>
        <p class="page-subtitle">
          R 线 = 防御 sleeve 镜像(owner 飞书回报入账,含费成本,不挂市价);
          Z 线 = 制度红利(打新/转债/现金收益,已实现口径)。
          券商 App 才是价格与市值的真相,本页只显示系统账本。
        </p>
      </div>
      <div class="header-actions">
        <span v-if="payload" class="generated-at">更新于 {{ formatTime(payload.generated_at) }}</span>
        <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
      </div>
    </header>

    <article v-if="error" class="banner banner-error">加载失败:{{ error }}</article>

    <div v-if="payload" class="line-grid">
      <el-card class="line-card" shadow="never">
        <template #header><span class="card-title">R 线 · 防御 sleeve 镜像</span></template>
        <dl class="stat-list">
          <div class="stat">
            <dt>现金</dt>
            <dd>{{ money(r.cash) }}</dd>
          </div>
          <div class="stat">
            <dt>本金申报</dt>
            <dd>
              <span :class="['pill', r.opening_declared ? 'pill-success' : 'pill-warning']">
                {{ r.opening_declared ? '已申报' : '未申报(现金仅为累计变动)' }}
              </span>
            </dd>
          </div>
          <div class="stat">
            <dt>持仓成本合计</dt>
            <dd>{{ money(r.cost_value) }}</dd>
          </div>
          <div class="stat">
            <dt>已入账成交</dt>
            <dd>{{ r.fill_count }} 笔</dd>
          </div>
        </dl>
        <el-table v-if="r.positions.length" :data="r.positions" size="small" class="position-table">
          <el-table-column prop="code" label="代码" width="110" />
          <el-table-column label="股数" width="110" align="right">
            <template #default="{ row }">{{ row.volume }}</template>
          </el-table-column>
          <el-table-column label="含费均价" width="120" align="right">
            <template #default="{ row }">{{ row.avg_cost.toFixed(4) }}</template>
          </el-table-column>
          <el-table-column label="含费成本" align="right">
            <template #default="{ row }">{{ money(row.volume * row.avg_cost) }}</template>
          </el-table-column>
        </el-table>
        <p v-else class="empty-note">(无持仓)</p>
      </el-card>

      <el-card class="line-card" shadow="never">
        <template #header><span class="card-title">Z 线 · 制度红利(已实现)</span></template>
        <dl class="stat-list">
          <div class="stat stat-primary">
            <dt>实现收益累计</dt>
            <dd>{{ money(z.realized_pnl) }}</dd>
          </div>
          <div class="stat">
            <dt>打新卖出</dt>
            <dd>{{ money(z.ipo_sell) }}</dd>
          </div>
          <div class="stat">
            <dt>转债卖出</dt>
            <dd>{{ money(z.cb_sell) }}</dd>
          </div>
          <div class="stat">
            <dt>现金收益</dt>
            <dd>{{ money(z.cash_yield) }}</dd>
          </div>
          <div class="stat">
            <dt>中签成本(打新 / 转债,非损益)</dt>
            <dd>{{ money(z.ipo_win) }} / {{ money(z.cb_win) }}</dd>
          </div>
          <div class="stat">
            <dt>记录数</dt>
            <dd>{{ z.records }} 条</dd>
          </div>
        </dl>
      </el-card>
    </div>

    <el-card v-if="payload" class="line-card" shadow="never">
      <template #header>
        <span class="card-title">最近成交与修正(最新在前,按回报顺序)</span>
      </template>
      <el-table v-if="rows.length" :data="rows" size="small" class="ledger-table">
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <span :class="['pill', kindPill(row.kind)]">{{ kindLabel(row.kind) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatTime(row.executed_at ?? row.recorded_at) }}</template>
        </el-table-column>
        <el-table-column label="内容" min-width="260">
          <template #default="{ row }">{{ describeRow(row) }}</template>
        </el-table-column>
        <el-table-column label="备注" min-width="160">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
      </el-table>
      <p v-else class="empty-note">(账本尚无成交、入金或修正记录)</p>
    </el-card>

    <el-card v-if="payload" class="line-card" shadow="never">
      <template #header>
        <span class="card-title">月度执行偏差(镜像实际成交 vs 研究侧收盘假设;正 = 实际更差)</span>
      </template>
      <el-table v-if="drift.length" :data="drift" size="small" class="drift-table">
        <el-table-column prop="month" label="月份" width="100" />
        <el-table-column prop="comparable_fills" label="可比笔数" width="100" align="right" />
        <el-table-column prop="uncovered_fills" label="未覆盖笔数" width="110" align="right" />
        <el-table-column label="偏差(元)" width="140" align="right">
          <template #default="{ row }">{{ signed(row.drift_yuan) }}</template>
        </el-table-column>
        <el-table-column label="偏差(%)" align="right">
          <template #default="{ row }">{{ signed(row.drift_pct, 4) }}%</template>
        </el-table-column>
      </el-table>
      <p v-else class="empty-note">(尚无镜像成交,无可披露偏差)</p>
    </el-card>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElCard, ElTable, ElTableColumn } from 'element-plus'
import { accountLinesApi, type AccountLinesPayload, type LedgerRow } from '@/api/accountLines'
import { describeLedgerRow, formatMoney, formatSigned, kindLabel, kindPill } from '@/utils/accountLines'

const loading = ref(false)
const error = ref<string | null>(null)
const payload = ref<AccountLinesPayload | null>(null)

const r = computed(() => payload.value!.r_line)
const z = computed(() => payload.value!.z_line)
const rows = computed(() => payload.value?.recent_ledger_rows ?? [])
const drift = computed(() => payload.value?.monthly_drift ?? [])

const money = formatMoney
const signed = formatSigned
const describeRow = (row: LedgerRow) => describeLedgerRow(row)

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

async function refresh() {
  loading.value = true
  error.value = null
  try {
    payload.value = await accountLinesApi.get()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
.account-lines {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.generated-at {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.page-subtitle {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.line-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
}

.card-title {
  font-weight: 600;
}

.stat-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px 24px;
  margin: 0 0 12px;
}

.stat dt {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.stat dd {
  margin: 2px 0 0;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}

.stat-primary dd {
  font-size: 22px;
  font-weight: 600;
}

.empty-note {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.banner {
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 13px;
}

.banner-error {
  background: var(--el-color-danger-light-9);
  border-left: 4px solid var(--el-color-danger);
  color: var(--el-color-danger);
}

.pill {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.pill-info {
  background: var(--el-color-info-light-9);
  color: var(--el-color-info);
}

.pill-warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.pill-success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}
</style>
