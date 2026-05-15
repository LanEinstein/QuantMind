<template>
  <section class="instruction-plans">
    <header class="page-header">
      <div>
        <h2 class="page-title">InstructionPlan 池</h2>
        <p class="page-subtitle">
          P1-5 §1.5 锁定:三层 reason 抽屉 Builder / RiskEngine / MockBroker 命名空间隔离;
          ``price_limit_violation_at_fill`` 仅出现在 MockBroker 抽屉。
        </p>
      </div>
      <div class="page-actions">
        <el-select
          v-model="statusFilter"
          placeholder="状态"
          clearable
          size="small"
          style="width: 140px"
          @change="reload"
        >
          <el-option
            v-for="opt in STATUS_OPTIONS"
            :key="opt"
            :label="opt"
            :value="opt"
          />
        </el-select>
        <el-input
          v-model="tradeDateFilter"
          placeholder="trade_date (YYYY-MM-DD)"
          size="small"
          clearable
          style="width: 200px"
          @change="reload"
        />
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <div v-if="repositoryStatus === 'unavailable'" class="banner banner-info">
      仓库尚未接线(Phase F 完成后真实 InstructionPlanRepository 上线),当前空列表为正常状态。
    </div>
    <div v-if="error" class="banner banner-error">
      加载失败:{{ error }}
    </div>

    <el-table
      :data="plans"
      stripe
      class="plans-table"
      empty-text="暂无 InstructionPlan"
      @row-click="onRowClick"
    >
      <el-table-column prop="instruction_id" label="instruction_id" min-width="260">
        <template #default="{ row }">
          <span class="instruction-id">{{ row.instruction_id }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="stock_code" label="标的" width="120">
        <template #default="{ row }">
          {{ row.stock_code }} {{ row.stock_name }}
        </template>
      </el-table-column>
      <el-table-column prop="side" label="方向" width="80">
        <template #default="{ row }">
          <span :class="['side-tag', sideClass(row.side)]">{{ row.side }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <span :class="['status-tag', statusClass(row.status)]">{{ row.status }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="volume" label="股数" width="100" />
      <el-table-column prop="limit_price" label="限价" width="100" />
      <el-table-column prop="valid_until" label="有效期至" width="180" />
      <el-table-column prop="trade_date" label="trade_date" width="120" />
    </el-table>

    <el-drawer
      v-model="drawerVisible"
      :title="drawerTitle"
      direction="rtl"
      size="60%"
      destroy-on-close
    >
      <div v-if="detail" class="drawer-body">
        <section class="drawer-summary">
          <h3 class="drawer-section-title">指令摘要</h3>
          <dl class="kv-list">
            <dt>方向</dt><dd>{{ detail.plan.side }}</dd>
            <dt>状态</dt><dd>{{ detail.plan.status }}</dd>
            <dt>标的</dt><dd>{{ detail.plan.stock_code }} {{ detail.plan.stock_name }}</dd>
            <dt>股数</dt><dd>{{ detail.plan.volume ?? '—' }}</dd>
            <dt>限价</dt><dd>{{ detail.plan.limit_price ?? '—' }}</dd>
            <dt>有效期至</dt><dd>{{ detail.plan.valid_until }}</dd>
            <dt>辩论轮次</dt><dd>{{ detail.debate_round_count }}</dd>
            <dt>失效条件</dt><dd>{{ detail.invalidation_summary }}</dd>
            <dt v-if="detail.plan.rejection_reason">拒绝原因</dt>
            <dd v-if="detail.plan.rejection_reason">{{ detail.plan.rejection_reason }}</dd>
          </dl>
        </section>

        <el-tabs v-model="activeTab" class="reason-tabs">
          <el-tab-pane label="Builder 五道早返" name="builder_early_return">
            <p class="tab-hint">
              五道早返:mode_switch / reconciliation_ticket_open / circuit_breaker_cooldown / data_quality_breach / watchlist_exclusion。
            </p>
            <el-empty
              v-if="detail.reason_tabs.builder_early_return.length === 0"
              description="该指令未触发 Builder 早返"
              :image-size="80"
            />
            <el-table v-else :data="builderRows" stripe>
              <el-table-column prop="reason_namespace" label="reason_namespace" min-width="240" />
              <el-table-column prop="at" label="at" width="220" />
              <el-table-column label="payload">
                <template #default="{ row }">
                  <pre class="payload-pre">{{ JSON.stringify(row.payload) }}</pre>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="RiskEngine 14-check" name="risk_engine_check">
            <p class="tab-hint">
              14 条独立规则,passed=null 表示该检查在当前模式下未启用。
            </p>
            <el-table :data="riskRows" stripe>
              <el-table-column prop="check_id" label="#" width="60" />
              <el-table-column prop="rule_name" label="rule_name" min-width="200" />
              <el-table-column label="passed" width="100">
                <template #default="{ row }">
                  <span :class="['pass-pill', passClass(row.passed)]">
                    {{ passLabel(row.passed) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="threshold" label="threshold" width="160" />
              <el-table-column prop="actual" label="actual" width="160" />
              <el-table-column prop="message" label="message" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="MockBroker at-fill" name="broker_at_fill">
            <p class="tab-hint">
              namespace 锁定:price_limit_violation_at_fill 只在此 tab 出现,与 RiskEngine 的 limit_up_block / limit_down_block 不混用。
            </p>
            <el-empty
              v-if="!detail.reason_tabs.broker_at_fill"
              description="尚无 MockBroker 终态记录(指令未派发或未撮合)"
              :image-size="80"
            />
            <dl v-else class="kv-list broker-kv">
              <dt>outcome</dt><dd>{{ detail.reason_tabs.broker_at_fill.outcome }}</dd>
              <dt v-if="detail.reason_tabs.broker_at_fill.reason">reason</dt>
              <dd v-if="detail.reason_tabs.broker_at_fill.reason">
                <span class="broker-reason-tag">
                  {{ detail.reason_tabs.broker_at_fill.reason }}
                </span>
              </dd>
              <dt>fill_price</dt>
              <dd>{{ detail.reason_tabs.broker_at_fill.fill_price ?? '—' }}</dd>
              <dt>fill_volume</dt>
              <dd>{{ detail.reason_tabs.broker_at_fill.fill_volume ?? '—' }}</dd>
            </dl>
          </el-tab-pane>
        </el-tabs>
      </div>
      <div v-else-if="detailLoading" class="drawer-loading">加载中…</div>
      <div v-else-if="detailError" class="drawer-loading">详情加载失败:{{ detailError }}</div>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { instructionPlansApi } from '@/api/instructionPlans'
import type {
  InstructionPlanDetailPayload,
  InstructionPlanSummary,
} from '@/types/instructionPlan'

const STATUS_OPTIONS = [
  'DRAFT',
  'VALIDATED',
  'REJECTED',
  'DISPATCHED',
  'FILLED',
  'EXPIRED',
  'AMBIGUOUS',
]

const plans = ref<InstructionPlanSummary[]>([])
const repositoryStatus = ref<'ok' | 'unavailable'>('unavailable')
const loading = ref(false)
const error = ref<string | null>(null)
const statusFilter = ref<string | undefined>(undefined)
const tradeDateFilter = ref<string | undefined>(undefined)

const drawerVisible = ref(false)
const detail = ref<InstructionPlanDetailPayload | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)
const activeTab = ref<'builder_early_return' | 'risk_engine_check' | 'broker_at_fill'>(
  'risk_engine_check',
)

const drawerTitle = computed(() =>
  detail.value
    ? `${detail.value.plan.instruction_id} · ${detail.value.plan.stock_name}`
    : '指令详情',
)

const builderRows = computed(() =>
  detail.value ? [...detail.value.reason_tabs.builder_early_return] : [],
)
const riskRows = computed(() =>
  detail.value ? [...detail.value.reason_tabs.risk_engine_check] : [],
)

async function reload(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const params: Record<string, string | number> = {}
    if (statusFilter.value) params.status = statusFilter.value
    if (tradeDateFilter.value) params.trade_date = tradeDateFilter.value
    const payload = await instructionPlansApi.list(params)
    plans.value = [...payload.plans]
    repositoryStatus.value = payload.repository_status
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load plans'
  } finally {
    loading.value = false
  }
}

async function onRowClick(row: InstructionPlanSummary): Promise<void> {
  detailLoading.value = true
  detailError.value = null
  detail.value = null
  drawerVisible.value = true
  activeTab.value = 'risk_engine_check'
  try {
    detail.value = await instructionPlansApi.get(row.instruction_id)
  } catch (err: unknown) {
    detailError.value = err instanceof Error ? err.message : 'failed to load detail'
  } finally {
    detailLoading.value = false
  }
}

function sideClass(side: string): string {
  if (side === 'BUY') return 'buy'
  if (side === 'SELL') return 'sell'
  return 'hold'
}

function statusClass(status: string): string {
  if (status === 'FILLED' || status === 'VALIDATED') return 'positive'
  if (status === 'REJECTED' || status === 'EXPIRED' || status === 'AMBIGUOUS') return 'negative'
  return 'neutral'
}

function passClass(passed: boolean | null): string {
  if (passed === true) return 'pass'
  if (passed === false) return 'fail'
  return 'unknown'
}

function passLabel(passed: boolean | null): string {
  if (passed === true) return 'PASS'
  if (passed === false) return 'FAIL'
  return 'n/a'
}

onMounted(() => {
  reload()
})
</script>

<style lang="scss" scoped>
.instruction-plans {
  padding: $gap-md $gap-lg;
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: $gap-md;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: $text-primary;
  margin: 0;
}
.page-subtitle {
  font-size: 12px;
  color: $text-muted;
  margin: 4px 0 0;
  max-width: 720px;
}
.page-actions {
  display: flex;
  gap: $gap-sm;
}

.banner {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  border: 1px solid transparent;
}
.banner-info {
  background: rgba(68, 138, 255, 0.10);
  border-color: rgba(68, 138, 255, 0.4);
  color: $color-accent-light;
}
.banner-error {
  background: rgba(255, 23, 68, 0.12);
  border-color: rgba(255, 23, 68, 0.3);
  color: $status-red;
}

.plans-table {
  flex-shrink: 0;
}

.instruction-id {
  font-family: 'Roboto Mono', monospace;
  font-size: 12px;
  color: $text-secondary;
}

.side-tag,
.status-tag,
.pass-pill {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
}
.side-tag.buy { background: rgba(255, 23, 68, 0.15); color: $color-up; }
.side-tag.sell { background: rgba(0, 200, 83, 0.15); color: $color-down; }
.side-tag.hold { background: rgba(142, 142, 160, 0.18); color: $text-muted; }

.status-tag.positive { background: rgba(0, 200, 83, 0.15); color: $color-down; }
.status-tag.negative { background: rgba(255, 23, 68, 0.15); color: $color-up; }
.status-tag.neutral { background: rgba(142, 142, 160, 0.18); color: $text-muted; }

.pass-pill.pass { background: rgba(0, 200, 83, 0.18); color: $status-green; }
.pass-pill.fail { background: rgba(255, 23, 68, 0.18); color: $status-red; }
.pass-pill.unknown { background: rgba(142, 142, 160, 0.18); color: $text-muted; }

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
  padding: 0 4px;
}

.drawer-section-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 $gap-sm;
}

.kv-list {
  display: grid;
  grid-template-columns: 100px 1fr;
  gap: 4px 12px;
  margin: 0;
  dt { color: $text-muted; font-size: 12px; }
  dd { color: $text-primary; font-size: 12px; margin: 0; }
}

.broker-kv dt { width: 110px; }
.broker-reason-tag {
  font-family: 'Roboto Mono', monospace;
  background: rgba(255, 23, 68, 0.18);
  color: $status-red;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 12px;
}

.reason-tabs {
  margin-top: $gap-md;
}
.tab-hint {
  font-size: 12px;
  color: $text-muted;
  margin: 4px 0 12px;
}

.payload-pre {
  margin: 0;
  font-family: 'Roboto Mono', monospace;
  font-size: 11px;
  color: $text-secondary;
  white-space: pre-wrap;
  word-break: break-all;
}

.drawer-loading {
  padding: 40px;
  text-align: center;
  color: $text-muted;
  font-size: 13px;
}
</style>
