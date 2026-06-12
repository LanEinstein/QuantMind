<template>
  <el-card shadow="never" class="dual-line-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">双线每日并行运行态</span>
        <el-button text size="small" :loading="loading" @click="fetchStatus">
          刷新
        </el-button>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <div v-else-if="!payload" class="placeholder-text">加载中…</div>
    <div v-else class="dual-line-grid">
      <article class="line-card">
        <header class="line-head">
          <span class="line-name">{{ payload.line1.label }}</span>
          <span :class="['live-dot', payload.line1.wired ? 'on' : 'off']">
            {{ payload.line1.wired ? '运行中' : '未接线' }}
          </span>
        </header>
        <dl class="line-kv">
          <dt>每日辩论上限</dt>
          <dd>{{ payload.line1.max_debates_per_day ?? '—' }}</dd>
        </dl>
      </article>

      <article class="line-card">
        <header class="line-head">
          <span class="line-name">{{ payload.line2.label }}</span>
        </header>
        <dl class="line-kv">
          <dt>日线监控</dt>
          <dd>
            <span :class="['live-dot', payload.line2.daily_wired ? 'on' : 'off']">
              {{ payload.line2.daily_wired ? '运行中' : '未接线' }}
            </span>
          </dd>
          <dt>盘中监控</dt>
          <dd>
            <span :class="['live-dot', payload.line2.intraday_wired ? 'on' : 'off']">
              {{ payload.line2.intraday_wired ? '运行中' : '未接线' }}
            </span>
          </dd>
        </dl>
      </article>

      <article class="line-card">
        <header class="line-head">
          <span class="line-name">{{ payload.rotation.label }}</span>
          <span :class="['live-dot', payload.rotation.wired ? 'on' : 'off']">
            {{ payload.rotation.wired ? '运行中' : '未接线' }}
          </span>
        </header>
        <dl class="line-kv">
          <dt>持仓槽上限</dt>
          <dd>{{ payload.rotation.max_total_positions ?? '—' }}</dd>
        </dl>
      </article>
    </div>

    <p v-if="payload" class="dual-line-foot">
      <span :class="['live-dot', payload.scheduler_wired ? 'on' : 'off']">
        调度器 {{ payload.scheduler_wired ? '运行中' : '未接线' }}
      </span>
      <span class="dual-line-note">{{ payload.note }}</span>
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { dualLineStatusApi } from '@/api/dualLineStatus'
import type { DualLineStatusPayload } from '@/types/dualLineStatus'

const payload = ref<DualLineStatusPayload | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

async function fetchStatus(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    payload.value = await dualLineStatusApi.get()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load status'
  } finally {
    loading.value = false
  }
}

onMounted(fetchStatus)

defineExpose({ fetchStatus })
</script>

<style lang="scss" scoped>
.dual-line-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: $gap-md;
}
.line-card {
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 12px;
}
.line-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: $gap-sm;
}
.line-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
}
.line-kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
  dt {
    color: $text-muted;
    font-size: 12px;
  }
  dd {
    color: $text-primary;
    font-size: 12px;
    margin: 0;
  }
}
.live-dot {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
}
.live-dot.on {
  background: rgba(0, 200, 83, 0.18);
  color: $status-green;
}
.live-dot.off {
  background: rgba(142, 142, 160, 0.18);
  color: $text-muted;
}
.dual-line-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: $gap-md 0 0;
}
.dual-line-note {
  font-size: 11px;
  color: $text-muted;
  line-height: 1.5;
}
.placeholder-text {
  padding: 24px;
  text-align: center;
  color: $text-muted;
  font-size: 13px;
}
.error-text {
  color: $status-red;
}
</style>
