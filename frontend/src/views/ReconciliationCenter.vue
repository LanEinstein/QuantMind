<template>
  <section class="reconciliation-center">
    <header class="page-header">
      <div>
        <h2 class="page-title">对账裁定中心</h2>
        <p class="page-subtitle">
          P0-5 §1.5 锁定:OPEN / EXPIRED ticket 是 5 大冻结源之一;
          三选一裁定(系统镜像 / 用户回报 / 对账更正)走唯一写入端点
          POST /api/reconciliation-tickets/&#123;id&#125;/decide,applier
          成功后才会持久化 RESOLVED_* 状态(fail-closed)。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </header>

    <article v-if="serviceStatus === 'unavailable'" class="banner banner-info">
      ReconciliationOrchestrator 尚未接线(Phase I-001 集成),当前无法列出 ticket。
    </article>

    <article v-if="error" class="banner banner-error">加载失败:{{ error }}</article>

    <article v-if="tickets.length === 0 && serviceStatus === 'ok'" class="empty-card">
      当前交易日没有未裁定的对账 ticket。
    </article>

    <article
      v-for="ticket in tickets"
      :key="ticket.ticket_id"
      class="ticket-card"
    >
      <header class="ticket-header">
        <span class="ticket-id">{{ ticket.ticket_id }}</span>
        <span :class="['status-pill', statusClass(ticket.status)]">
          {{ ticket.status }}
        </span>
        <span class="ticket-meta">
          trade_date {{ ticket.trade_date }} · created
          {{ formatTime(ticket.created_at) }}
        </span>
      </header>

      <section class="diff-section">
        <h4 class="section-title">差异明细</h4>
        <el-table :data="ticket.deviation_report.deviations" stripe size="small">
          <el-table-column prop="field" label="字段" width="160" />
          <el-table-column prop="expected" label="系统镜像" min-width="160" />
          <el-table-column prop="actual" label="用户回报" min-width="160" />
          <el-table-column label="差额" width="140" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.abs_diff) }}
            </template>
          </el-table-column>
          <el-table-column label="阈值" width="100" align="right">
            <template #default="{ row }">
              {{ formatNumber(row.threshold) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <span :class="row.passed ? 'pill-pass' : 'pill-fail'">
                {{ row.passed ? '通过' : '超阈' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="decision-section">
        <h4 class="section-title">三选一裁定</h4>
        <div class="decision-buttons">
          <el-button
            type="primary"
            :loading="deciding[ticket.ticket_id] === 'RESOLVED_SYSTEM_AS_TRUTH'"
            :disabled="!canDecide(ticket)"
            @click="decide(ticket, 'RESOLVED_SYSTEM_AS_TRUTH')"
          >
            采纳系统镜像
          </el-button>
          <el-button
            type="warning"
            :loading="deciding[ticket.ticket_id] === 'RESOLVED_USER_AS_TRUTH'"
            :disabled="!canDecide(ticket)"
            @click="decide(ticket, 'RESOLVED_USER_AS_TRUTH')"
          >
            采纳用户回报
          </el-button>
          <el-button
            type="danger"
            :loading="deciding[ticket.ticket_id] === 'RESOLVED_AMENDED'"
            :disabled="!canDecide(ticket)"
            @click="openAmendDialog(ticket)"
          >
            对账更正 (amend)
          </el-button>
        </div>
        <p v-if="!canDecide(ticket)" class="decision-hint">
          ticket 已处于终态({{ ticket.status }}),无需重复裁定。
        </p>
      </section>

      <section v-if="resultByTicket[ticket.ticket_id]" class="result-section">
        <h4 class="section-title">裁定结果</h4>
        <div class="kv-row">
          <span>resolution</span>
          <span>{{ resultByTicket[ticket.ticket_id].status }}</span>
        </div>
        <div class="kv-row">
          <span>cash_delta</span>
          <span>{{ resultByTicket[ticket.ticket_id].apply_result.cash_delta }}</span>
        </div>
        <div class="kv-row">
          <span>broker_event_sequence</span>
          <span>{{ resultByTicket[ticket.ticket_id].apply_result.broker_event_sequence ?? '—' }}</span>
        </div>
        <div class="kv-row">
          <span>reason</span>
          <span>{{ resultByTicket[ticket.ticket_id].apply_result.reason }}</span>
        </div>
      </section>
    </article>

    <el-dialog
      v-model="amendDialogVisible"
      title="对账更正:输入新的 broker 镜像"
      width="600px"
    >
      <article class="amend-dialog">
        <p class="amend-hint">
          仅当系统镜像与用户回报都不正确时使用。amend snapshot 将作为新基线,
          应用后 broker 立即 reset_to_snapshot,且 ticket 状态变为 RESOLVED_AMENDED。
        </p>
        <el-input
          v-model="amendJsonText"
          type="textarea"
          :rows="10"
          placeholder='{ "cash": 100000, "snapshot_at": "2026-05-16T16:00:00+00:00", "positions": [{ "code": "600519", "volume": 100, "cost_price": 1800.5 }] }'
        />
        <p v-if="amendParseError" class="amend-error">{{ amendParseError }}</p>
      </article>
      <template #footer>
        <el-button @click="amendDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="amendSubmitting"
          @click="submitAmend"
        >
          应用 amend
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  ElButton,
  ElDialog,
  ElInput,
  ElTable,
  ElTableColumn,
} from 'element-plus'
import {
  reconciliationApi,
  type ReconciliationTicket,
  type ReconciliationTicketStatus,
  type DecisionResultPayload,
  type MockBrokerSnapshot,
} from '@/api/reconciliation'

const tickets = ref<ReconciliationTicket[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const serviceStatus = ref<'ok' | 'unavailable'>('ok')
const deciding = reactive<Record<string, ReconciliationTicketStatus | null>>({})
const resultByTicket = reactive<Record<string, DecisionResultPayload>>({})

const amendDialogVisible = ref(false)
const amendJsonText = ref('')
const amendParseError = ref<string | null>(null)
const amendSubmitting = ref(false)
const amendTargetTicket = ref<ReconciliationTicket | null>(null)

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

function formatNumber(n: number): string {
  if (Number.isNaN(n)) return '—'
  return n.toFixed(4)
}

function statusClass(status: ReconciliationTicketStatus): string {
  switch (status) {
    case 'OPEN':
      return 'pill-warning'
    case 'EXPIRED':
      return 'pill-danger'
    default:
      return 'pill-success'
  }
}

function canDecide(ticket: ReconciliationTicket): boolean {
  return ticket.status === 'OPEN' || ticket.status === 'EXPIRED'
}

async function refresh() {
  loading.value = true
  error.value = null
  try {
    const payload = await reconciliationApi.list()
    serviceStatus.value = payload.status
    tickets.value = payload.tickets
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function decide(
  ticket: ReconciliationTicket,
  resolution:
    | 'RESOLVED_SYSTEM_AS_TRUTH'
    | 'RESOLVED_USER_AS_TRUTH'
    | 'RESOLVED_AMENDED',
  amended_snapshot: MockBrokerSnapshot | null = null,
): Promise<boolean> {
  /**
   * Apply a single decision. Returns ``true`` when the POST itself
   * succeeded (broker mirror was reset + ticket is now RESOLVED) and
   * ``false`` only when the POST raised.
   *
   * ``refresh()`` is intentionally best-effort: when the decision POST
   * lands but the follow-up list reload fails (network blip, mongo
   * down), the broker has already committed the reset — telling the
   * operator the amendment "failed" would be misleading and prompt a
   * dangerous retry. Surface the reload failure separately (codex
   * cycle 2 P2 RESOLVED).
   */
  deciding[ticket.ticket_id] = resolution
  let postOk = false
  try {
    const result = await reconciliationApi.decide(ticket.ticket_id, {
      resolution,
      amended_snapshot,
    })
    resultByTicket[ticket.ticket_id] = result
    postOk = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    deciding[ticket.ticket_id] = null
  }

  if (postOk) {
    // Decision was applied — best-effort list refresh; do NOT flip
    // ``postOk`` back to false if it fails (broker mirror is already
    // committed). Errors surface in ``error.value`` so the operator
    // sees a warning banner.
    try {
      await refresh()
    } catch (err) {
      error.value =
        err instanceof Error
          ? `刷新失败 (裁定已应用):${err.message}`
          : String(err)
    }
  }
  return postOk
}

function openAmendDialog(ticket: ReconciliationTicket): void {
  amendTargetTicket.value = ticket
  amendJsonText.value = ''
  amendParseError.value = null
  amendDialogVisible.value = true
}

async function submitAmend(): Promise<void> {
  if (!amendTargetTicket.value) return
  let parsed: MockBrokerSnapshot
  try {
    parsed = JSON.parse(amendJsonText.value) as MockBrokerSnapshot
  } catch (err) {
    amendParseError.value = `JSON 解析失败:${err instanceof Error ? err.message : String(err)}`
    return
  }
  amendSubmitting.value = true
  amendParseError.value = null
  try {
    const ok = await decide(amendTargetTicket.value, 'RESOLVED_AMENDED', parsed)
    if (ok) {
      // Only close on success — the dialog stays open with the
      // typed JSON visible so the operator can correct + retry
      // without re-pasting (codex cycle 1 P2 RESOLVED).
      amendDialogVisible.value = false
    } else {
      amendParseError.value =
        error.value || 'amend 提交失败,请检查后端日志或调整 JSON 后重试。'
    }
  } catch (err) {
    // Defensive — decide() catches all errors internally, but a
    // pre-await throw (e.g. inside reactive plumbing) would land here.
    amendParseError.value =
      err instanceof Error ? err.message : String(err)
  } finally {
    amendSubmitting.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
.reconciliation-center {
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

.empty-card,
.ticket-card {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 16px 20px;
}

.empty-card {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.ticket-header {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.ticket-id {
  font-family: monospace;
  font-weight: 600;
}

.status-pill {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
}

.pill-warning {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.pill-danger {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.pill-success {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.ticket-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.section-title {
  margin: 16px 0 8px;
  font-size: 14px;
  font-weight: 600;
}

.decision-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.decision-hint {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.pill-pass {
  color: var(--el-color-success);
  font-weight: 600;
}

.pill-fail {
  color: var(--el-color-danger);
  font-weight: 600;
}

.result-section {
  background: var(--el-fill-color-light);
  border-radius: 4px;
  padding: 12px 16px;
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

.amend-dialog {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.amend-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin: 0;
}

.amend-error {
  color: var(--el-color-danger);
  font-size: 13px;
  margin: 0;
}
</style>
