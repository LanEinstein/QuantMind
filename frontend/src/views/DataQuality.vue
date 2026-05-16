<template>
  <section class="data-quality">
    <header class="page-header">
      <div>
        <h2 class="page-title">数据质量(P0-8 / P1-2.B)</h2>
        <p class="page-subtitle">
          7 + 3 项 DataQualityState;4 项 blocking breach 任意命中即 builder
          早返降级 HOLD(P0-8 §1.5.1);news / MiroFish / snapshot outage
          维持非阻断(加分项,P0-8 §2 红线 11)。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </header>

    <article class="lookup-card">
      <el-input
        v-model="stockCode"
        placeholder="输入 6 位 watchlist 股票代码(如 600519)"
        clearable
        @keyup.enter="refresh"
      />
      <el-button type="primary" :loading="loading" @click="refresh">查询</el-button>
    </article>

    <article v-if="error" class="banner banner-error">{{ error }}</article>

    <article v-if="payload?.status === 'unavailable'" class="banner banner-info">
      DataQualityProvider 尚未接线(C-005 / C-006 落地后启用),无法返回数据。
    </article>

    <article v-if="payload?.state" class="state-card">
      <header class="state-header">
        <span>{{ payload.state.stock_code }}</span>
        <span :class="['pill', acceptablePill]">
          {{ payload.state.is_acceptable_for_buy_sell ? '可接受买卖' : '不可接受' }}
        </span>
      </header>
      <div class="state-grid">
        <div class="kv-row">
          <span>评估时间</span><span>{{ payload.state.evaluated_at ?? '—' }}</span>
        </div>
        <div class="kv-row">
          <span>主行情 age</span>
          <span>{{ formatAge(payload.state.primary_quote_age_seconds) }}</span>
        </div>
        <div class="kv-row">
          <span>备份 age</span>
          <span>{{ formatAge(payload.state.backup_quote_age_seconds) }}</span>
        </div>
        <div class="kv-row">
          <span>新闻源存活数</span>
          <span>{{ payload.state.news_sources_alive_count ?? '—' }} / 5</span>
        </div>
        <div class="kv-row">
          <span>quote_unavailable(阻断)</span>
          <span :class="breachPill(payload.state.quote_unavailable)">
            {{ payload.state.quote_unavailable ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>quote_staleness_breach(阻断)</span>
          <span :class="breachPill(payload.state.quote_staleness_breach)">
            {{ payload.state.quote_staleness_breach ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>quote_divergence_breach(阻断)</span>
          <span :class="breachPill(payload.state.quote_divergence_breach)">
            {{ payload.state.quote_divergence_breach ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>minimum_freshness_breach(阻断)</span>
          <span :class="breachPill(payload.state.minimum_freshness_breach)">
            {{ payload.state.minimum_freshness_breach ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>news_outage_breach(非阻断)</span>
          <span :class="payload.state.news_outage_breach ? 'pill-warning' : 'pill-success'">
            {{ payload.state.news_outage_breach ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>mirofish_unavailable(非阻断)</span>
          <span :class="payload.state.mirofish_unavailable ? 'pill-warning' : 'pill-success'">
            {{ payload.state.mirofish_unavailable ? '是' : '否' }}
          </span>
        </div>
        <div class="kv-row">
          <span>watchlist_snapshot_outage(非阻断)</span>
          <span :class="payload.state.watchlist_snapshot_outage ? 'pill-warning' : 'pill-success'">
            {{ payload.state.watchlist_snapshot_outage ? '是' : '否' }}
          </span>
        </div>
      </div>

      <section class="blocking-section">
        <h4 class="section-title">Blocking breaches</h4>
        <ul v-if="payload.state.blocking_breaches?.length" class="breach-list">
          <li v-for="b in payload.state.blocking_breaches" :key="b">{{ b }}</li>
        </ul>
        <p v-else class="empty">无阻断项。</p>
      </section>

      <section v-if="payload.state.degradation_reason" class="blocking-section">
        <h4 class="section-title">Degradation reason</h4>
        <code class="reason-cell">{{ payload.state.degradation_reason }}</code>
      </section>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import { dataQualityApi, type DataQualityPayload } from '@/api/dataQuality'

const stockCode = ref('600519')
const loading = ref(false)
const error = ref<string | null>(null)
const payload = ref<DataQualityPayload | null>(null)

const acceptablePill = computed(() =>
  payload.value?.state?.is_acceptable_for_buy_sell ? 'pill-success' : 'pill-warning',
)

function breachPill(value: boolean | null | undefined): string {
  return value ? 'pill-danger' : 'pill-success'
}

function formatAge(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (value < 60) return `${value.toFixed(1)}s`
  if (value < 3600) return `${(value / 60).toFixed(1)}m`
  return `${(value / 3600).toFixed(1)}h`
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(2)}%`
}

async function refresh() {
  if (!/^\d{6}$/.test(stockCode.value)) {
    error.value = 'stock_code 必须是 6 位数字'
    return
  }
  loading.value = true
  error.value = null
  payload.value = null
  try {
    payload.value = await dataQualityApi.get(stockCode.value)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.data-quality {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
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

.lookup-card {
  display: flex;
  gap: 12px;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
}

.banner {
  border-radius: 6px;
  padding: 10px 14px;
  font-size: 13px;
}

.banner-info {
  background: var(--el-color-info-light-9);
  border-left: 4px solid var(--el-color-info);
  color: var(--el-color-info);
}

.banner-error {
  background: var(--el-color-danger-light-9);
  border-left: 4px solid var(--el-color-danger);
  color: var(--el-color-danger);
}

.state-card {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 16px 20px;
}

.state-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}

.state-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 4px;
  margin-top: 12px;
}

.kv-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

.kv-row:last-child {
  border-bottom: none;
}

.pill {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
}

.pill-success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.pill-warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.pill-danger {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.reason-cell {
  display: inline-block;
  padding: 4px 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
  word-break: break-all;
}

.section-title {
  margin: 16px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.breach-list {
  margin: 0;
  padding-left: 20px;
}

.empty {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
