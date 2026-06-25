<template>
  <el-dialog
    :model-value="visible"
    title="记录手动操作(用户自主交易)"
    width="460px"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @open="onOpen"
  >
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="form-note"
      title="此为用户自主操作记录"
      description="记录你在系统未建议时自主执行的买卖。仅进模拟账本、不伪造系统指令、不计入系统能力评估;飞书将收到「已记录」回执。"
    />

    <el-form :model="form" label-width="92px" class="manual-trade-form">
      <el-form-item label="代码">
        <el-input v-model="form.code" :disabled="codeLocked" maxlength="6" placeholder="6 位代码" />
      </el-form-item>
      <el-form-item label="方向">
        <el-radio-group v-model="form.side">
          <el-radio-button value="BUY">买入</el-radio-button>
          <el-radio-button value="SELL">卖出</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="数量(股)">
        <el-input-number
          v-model="form.volume"
          :min="LOT"
          :step="LOT"
          :max="maxVolume"
          step-strictly
        />
        <span v-if="form.side === 'SELL' && sellableVolume !== null" class="hint">
          可卖 {{ sellableVolume }} 股(T+1)
        </span>
        <span
          v-else-if="form.side === 'SELL'"
          class="hint hint-warn"
        >
          ⚠ 可卖数量未知 — 请勿超过实际可卖,后端将按 T+1 可卖量校验
        </span>
      </el-form-item>
      <el-form-item label="成交价">
        <el-input-number v-model="form.price" :min="0.01" :step="0.01" :precision="2" />
      </el-form-item>
      <el-form-item label="成交时间">
        <el-date-picker
          v-model="form.executedAt"
          type="datetime"
          placeholder="成交时间"
          value-format="YYYY-MM-DDTHH:mm:ss"
        />
      </el-form-item>
      <el-form-item label="原因">
        <el-select v-model="form.reason" style="width: 100%">
          <el-option
            v-for="(label, key) in REASON_LABELS"
            :key="key"
            :value="key"
            :label="label"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="备注">
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="2"
          maxlength="256"
          show-word-limit
          placeholder="可选,display-only"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-popconfirm
        :title="confirmText"
        confirm-button-text="确认记录"
        cancel-button-text="再看看"
        width="280"
        @confirm="submit"
      >
        <template #reference>
          <el-button type="primary" :loading="submitting" :disabled="!canSubmit">
            记录
          </el-button>
        </template>
      </el-popconfirm>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { manualTradesApi, mintExternalTradeId } from '@/api/manualTrades'
import {
  MANUAL_TRADE_LOT_SIZE as LOT,
  MANUAL_TRADE_REASON_LABELS as REASON_LABELS,
  type ManualTradeReason,
  type ManualTradeSide,
} from '@/types/manualTrade'

interface Prefill {
  readonly code?: string
  readonly side?: ManualTradeSide
  readonly sellableVolume?: number | null
}

const props = defineProps<{
  visible: boolean
  prefill?: Prefill
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  recorded: []
}>()

const submitting = ref(false)

// AD-005 idempotency (codex P1): the external_trade_id is minted ONCE per
// form open and reused across retries. If the first POST applies the broker
// mutation but the browser sees a timeout, resubmitting the same dialog sends
// the SAME id so the backend's idempotency guard dedupes it — minting inside
// submit() would create a fresh id and double-apply the fill.
const externalId = ref('')

const form = reactive({
  code: '',
  side: 'SELL' as ManualTradeSide,
  volume: LOT,
  price: 0.01,
  executedAt: '',
  reason: 'USER_TAKE_PROFIT' as ManualTradeReason,
  note: '',
})

const codeLocked = computed(() => Boolean(props.prefill?.code))
const sellableVolume = computed(() =>
  props.prefill?.sellableVolume ?? null,
)
const maxVolume = computed(() =>
  form.side === 'SELL' && sellableVolume.value !== null
    ? // F8 (codex): the SELL cap is the sellable volume floored to a whole lot —
      // NOT Math.max(LOT, …), which rounded a sub-lot holding (e.g. 50 sellable)
      // UP to a full 100-lot and let the form over-sell. A sub-lot holding floors
      // to 0 → canSubmit blocks the lot-form SELL (the odd lot can't be lot-sold
      // here; the backend remains the authoritative available-volume gate).
      Math.floor(sellableVolume.value / LOT) * LOT
    : undefined,
)

const canSubmit = computed(() => {
  const base =
    /^\d{6}$/.test(form.code) &&
    form.volume >= LOT &&
    form.volume % LOT === 0 &&
    form.price > 0 &&
    Boolean(form.executedAt)
  if (!base) return false
  // F8 (production-hardening 2026-06-25): a SELL must not exceed the known T+1
  // sellable volume — an over-sell would write a position the ledger can't back.
  // When sellable is UNKNOWN (null → maxVolume undefined) we allow + warn (the
  // template shows ⚠), since the backend RiskEngine/broker is the authoritative
  // available-volume gate.
  if (form.side === 'SELL' && maxVolume.value !== undefined) {
    return form.volume <= maxVolume.value
  }
  return true
})

const confirmText = computed(
  () =>
    `确认记录:${form.side === 'BUY' ? '买入' : '卖出'} ${form.code} ` +
    `${form.volume} 股 @ ${form.price}?此操作将写入模拟账本。`,
)

function localDatetime(): string {
  const d = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

function onOpen(): void {
  form.code = props.prefill?.code ?? ''
  form.side = props.prefill?.side ?? 'SELL'
  form.volume = LOT
  form.price = 0.01
  form.executedAt = localDatetime()
  form.reason = 'USER_TAKE_PROFIT'
  form.note = ''
  // Mint the idempotency id once per open; retries reuse it.
  externalId.value = mintExternalTradeId(form.code, form.side, new Date())
}

// The UT- id embeds code+side, so re-mint when either changes (a different
// logical trade). Price/volume edits keep the same id, so a retry of the
// same trade dedupes on the backend; editing code/side mints a fresh id.
watch(
  () => [form.code, form.side] as const,
  () => {
    if (/^\d{6}$/.test(form.code)) {
      externalId.value = mintExternalTradeId(form.code, form.side, new Date())
    }
  },
)

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const resp = await manualTradesApi.submit({
      external_trade_id: externalId.value,
      code: form.code,
      side: form.side,
      volume: form.volume,
      price: form.price,
      executed_at: form.executedAt,
      reason: form.reason,
      note: form.note || undefined,
    })
    ElMessage.success(`已记录(${resp.external_trade_id})`)
    emit('recorded')
    emit('update:visible', false)
  } catch (err: unknown) {
    ElMessage.error(getErrorMessage(err))
  } finally {
    submitting.value = false
  }
}

function getErrorMessage(err: unknown): string {
  // Axios errors carry the backend envelope under response.data.detail.error.
  const maybe = err as { response?: { data?: { detail?: { error?: string } } } }
  const detail = maybe.response?.data?.detail?.error
  if (detail) return detail
  if (err instanceof Error) return err.message
  return '记录失败'
}
</script>

<style scoped lang="scss">
.form-note {
  margin-bottom: $gap-md;
}
.manual-trade-form .hint {
  margin-left: 10px;
  font-size: 12px;
  color: $text-muted;
}
.manual-trade-form .hint-warn {
  color: $status-yellow;
}
</style>
