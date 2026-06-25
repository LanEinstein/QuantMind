<template>
  <el-card shadow="never" class="autopilot-timeline">
    <template #header>
      <div class="card-header">
        <span class="card-title">自动驾驶链路 · 今日({{ today }})</span>
        <el-button text size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <template v-else>
      <div v-if="failedSources.length" class="placeholder-text error-text">
        数据获取失败:{{ failedSources.join('、') }}(对应阶段状态可能不准)
      </div>
    </template>
    <el-timeline v-if="!error" class="pipeline-timeline">
      <el-timeline-item
        v-for="stage in stages"
        :key="stage.key"
        :type="stage.active ? 'primary' : 'info'"
        :hollow="!stage.active"
        :timestamp="stage.timestamp"
        placement="top"
      >
        <div class="stage-row">
          <span class="stage-name">{{ stage.name }}</span>
          <span :class="['stage-summary', stage.active ? 'active' : 'idle']">
            {{ stage.summary }}
          </span>
        </div>
      </el-timeline-item>
    </el-timeline>

    <p class="timeline-foot">
      只读 · 复用现有轮询端点(instruction-plans / slot-rotation / acceptance /
      dual-line-status);全自动模拟盘的链路可观察性视图。
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { instructionPlansApi } from '@/api/instructionPlans'
import { slotRotationApi } from '@/api/slotRotation'
import { acceptanceApi } from '@/api/acceptance'
import { dualLineStatusApi } from '@/api/dualLineStatus'
import type { InstructionPlanSummary } from '@/types/instructionPlan'
import type { SlotRotationPayload } from '@/types/slotRotation'
import type { AcceptanceLatestPayload } from '@/types/acceptance'
import type { DualLineStatusPayload } from '@/types/dualLineStatus'

interface Stage {
  readonly key: string
  readonly name: string
  readonly summary: string
  readonly active: boolean
  readonly timestamp: string
}

function localToday(): string {
  // The owner's machine runs in the Shanghai trading clock; the local date
  // is the trade_date the backend keys on.
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

const today = localToday()
const plans = ref<readonly InstructionPlanSummary[]>([])
const rotation = ref<SlotRotationPayload | null>(null)
const acceptance = ref<AcceptanceLatestPayload | null>(null)
const dualLine = ref<DualLineStatusPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
// F10 (production-hardening 2026-06-25): the 3 secondary fetches used a bare
// `.catch(() => null)`, so a backend OUTAGE for any of them rendered as a normal
// idle/"未接线" stage — indistinguishable from a genuinely-not-yet-run pipeline.
// Track which secondary sources failed so the view can say "数据获取失败" instead
// of masking the outage as idle.
const failedSources = ref<string[]>([])

function earliestCreatedAt(items: readonly InstructionPlanSummary[]): string {
  if (!items.length) return ''
  return items.reduce(
    (min, p) => (p.created_at < min ? p.created_at : min),
    items[0].created_at,
  )
}

function hhmm(iso: string): string {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(11, 16)
}

const stages = computed<Stage[]>(() => {
  const todayPlans = plans.value.filter((p) => p.trade_date === today)
  const buys = todayPlans.filter((p) => p.side === 'BUY')
  const validated = todayPlans.filter((p) => p.status === 'VALIDATED')
  const filled = todayPlans.filter((p) => p.status === 'FILLED')
  const rejected = todayPlans.filter(
    (p) => p.status === 'REJECTED' || p.status === 'EXPIRED' || p.status === 'AMBIGUOUS',
  )
  const codes = new Set(todayPlans.map((p) => p.stock_code))
  const todayRotation = (rotation.value?.recent_events ?? []).filter(
    (e) => e.trade_date === today,
  )
  const acceptanceToday = acceptance.value?.report?.trade_date === today
  const firstAt = hhmm(earliestCreatedAt(todayPlans))

  return [
    {
      key: 'screening',
      name: '筛选',
      summary: todayPlans.length
        ? `全市场筛选已产出候选(${codes.size} 标的进入指令流)`
        : '今日尚未触发筛选(09:35 cron 前 / 非交易日)',
      active: todayPlans.length > 0,
      timestamp: firstAt,
    },
    {
      key: 'candidates',
      name: '候选',
      summary: `${codes.size} 只候选 · BUY ${buys.length} 条`,
      active: codes.size > 0,
      timestamp: '',
    },
    {
      key: 'debate',
      name: '辩论',
      summary: todayPlans.length
        ? `${todayPlans.length} 条经 4-agent 辩论(基本面/技术/风控/基金经理)`
        : '无辩论',
      active: todayPlans.length > 0,
      timestamp: '',
    },
    {
      key: 'instruction',
      name: '指令',
      summary: `VALIDATED ${validated.length} · 拒单/过期 ${rejected.length}`,
      active: validated.length > 0,
      timestamp: '',
    },
    {
      key: 'fill',
      name: '成交',
      summary: filled.length
        ? `${filled.length} 条已成交入账`
        : '尚无成交(模拟撮合 / 待人工回报)',
      active: filled.length > 0,
      timestamp: '',
    },
    {
      key: 'line2',
      name: 'Line-2 触发',
      summary: todayRotation.length
        ? `${todayRotation.length} 条轮动/监控事件`
        : dualLine.value?.line2.intraday_wired
          ? '持仓监控运行中,今日无触发'
          : '监控未接线',
      active: todayRotation.length > 0,
      timestamp: '',
    },
    {
      key: 'eod',
      name: '盘后复盘',
      summary: acceptanceToday
        ? `验收已计算(${acceptance.value?.report?.outcome})`
        : '待 16:00 盘后管线',
      active: acceptanceToday,
      timestamp: acceptanceToday ? hhmm(acceptance.value?.report?.computed_at ?? '') : '',
    },
  ]
})

async function reload(): Promise<void> {
  loading.value = true
  error.value = null
  // F10: reset + re-track per-source failures each reload. A secondary source
  // that 404s/500s/times out is recorded (not silently nulled), so the view can
  // surface "数据获取失败" rather than render it as a normal idle stage. Cleared
  // up-front (not only on the success path) so a primary-fetch reject can't leave
  // a stale failed list behind (codex F10).
  failedSources.value = []
  const failed: string[] = []
  const track = <T,>(label: string, p: Promise<T>): Promise<T | null> =>
    p.catch(() => {
      failed.push(label)
      return null
    })
  try {
    const [planPayload, rot, acc, dl] = await Promise.all([
      instructionPlansApi.list({ trade_date: today, limit: 200 }),
      track('轮动', slotRotationApi.get()),
      track('验收', acceptanceApi.getLatest()),
      track('双线状态', dualLineStatusApi.get()),
    ])
    plans.value = planPayload.plans
    rotation.value = rot
    acceptance.value = acc
    dualLine.value = dl
    failedSources.value = failed
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load pipeline'
  } finally {
    loading.value = false
  }
}

// F5 (production-hardening 2026-06-25): poll so the pipeline view stays live on
// a days-open dashboard instead of freezing on its mount snapshot.
const POLL_INTERVAL_MS = 60_000
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  void reload()
  pollTimer = setInterval(() => void reload(), POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

defineExpose({ reload })
</script>

<style scoped lang="scss">
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}
.pipeline-timeline {
  padding-left: 4px;
  margin-top: $gap-sm;
}
.stage-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.stage-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  min-width: 84px;
}
.stage-summary {
  font-size: 12px;
}
.stage-summary.active {
  color: $text-secondary;
}
.stage-summary.idle {
  color: $text-muted;
}
.timeline-foot {
  font-size: 11px;
  color: $text-muted;
  margin: $gap-sm 0 0;
}
.placeholder-text {
  color: $text-muted;
  font-size: 12px;
  padding: 8px 0;
}
.error-text {
  color: $status-red;
}
</style>
