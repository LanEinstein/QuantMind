<template>
  <el-card shadow="never" class="evolution-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">自进化透明面板</span>
        <el-button text size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </template>

    <div v-if="error" class="placeholder-text error-text">加载失败:{{ error }}</div>
    <template v-else>
      <p v-if="payload && payload.source === 'unavailable'" class="placeholder-text">
        实验注册表未接线(Mongo 未连接 / 尚无实验);当前展示当前生效 manifest。
      </p>

      <!-- Current activation manifest (R-001 LiveArtifactRegistry) -->
      <section class="evo-section">
        <h5 class="evo-subtitle">当前生效 manifest(已批准 artifact 哈希集)</h5>
        <div v-if="payload?.current_manifest" class="manifest-box">
          <span class="manifest-meta">
            version {{ payload.current_manifest.version }}
            <template v-if="payload.current_manifest.updated_at">
              · 更新于 {{ fmtTs(payload.current_manifest.updated_at) }}
            </template>
          </span>
          <ul class="manifest-list">
            <li v-for="(hashes, kind) in payload.current_manifest.approved" :key="kind">
              <span class="manifest-kind">{{ kind }}</span>
              <span class="manifest-count">{{ hashes.length }} 个已批准</span>
              <code v-if="hashes.length" class="manifest-hash">{{ shortHash(hashes[0]) }}…</code>
            </li>
          </ul>
        </div>
        <p v-else class="placeholder-text">manifest 不可用(lockfile 缺失 / 损坏)。</p>
      </section>

      <!-- Promotion-intent history (AB-003) -->
      <section class="evo-section">
        <h5 class="evo-subtitle">晋升/降级 intent 历史</h5>
        <el-table
          v-if="payload && payload.intents.length"
          :data="[...payload.intents]"
          size="small"
          stripe
          max-height="220"
        >
          <el-table-column prop="action" label="动作" width="90" />
          <el-table-column prop="kind" label="类型" min-width="120" />
          <el-table-column prop="family" label="family" min-width="120" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="intentTagType(row.status)" size="small" effect="plain">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最近事件" width="170">
            <template #default="{ row }">{{ fmtTs(row.last_event_at) }}</template>
          </el-table-column>
        </el-table>
        <p v-else class="placeholder-text">暂无晋升/降级 intent。</p>
      </section>

      <!-- Experiment registry (AB-001) — failures included -->
      <section class="evo-section">
        <h5 class="evo-subtitle">
          实验注册表(含失败实验,非仅赢家)
          <el-tag size="small" type="info" effect="plain">
            {{ payload?.experiments.length ?? 0 }} 条
          </el-tag>
        </h5>
        <el-table
          v-if="payload && payload.experiments.length"
          :data="[...payload.experiments]"
          size="small"
          stripe
          max-height="280"
        >
          <el-table-column label="结果" width="76">
            <template #default="{ row }">
              <el-tag :type="row.success ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.success ? '通过' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="kind" label="类型" min-width="120" />
          <el-table-column prop="family" label="family" min-width="120" />
          <el-table-column prop="hypothesis" label="假设" min-width="200" show-overflow-tooltip />
          <el-table-column label="窗口" width="120">
            <template #default="{ row }">
              {{ row.trading_days }}d / {{ row.sample_count }}样本
            </template>
          </el-table-column>
          <el-table-column label="登记于" width="170">
            <template #default="{ row }">{{ fmtTs(row.registered_at) }}</template>
          </el-table-column>
        </el-table>
        <p v-else class="placeholder-text">
          暂无实验登记(ChallengerReplayer 尚未实现,22:00 影子运行 audit 留痕)。
        </p>
      </section>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { evolutionApi } from '@/api/evolution'
import type { EvolutionHistoryPayload } from '@/types/evolution'

const payload = ref<EvolutionHistoryPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

function shortHash(hash: string): string {
  return hash.slice(0, 12)
}

function fmtTs(ts: string): string {
  // Display-only; keep the date + minute, drop sub-second noise.
  return ts.replace('T', ' ').slice(0, 16)
}

function intentTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
  switch (status) {
    case 'ACTIVATED':
      return 'success'
    case 'ROLLED_BACK':
    case 'CANCELLED':
      return 'danger'
    case 'FROZEN':
      return 'warning'
    default:
      return 'info'
  }
}

async function reload(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    payload.value = await evolutionApi.getHistory()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'failed to load evolution history'
  } finally {
    loading.value = false
  }
}

onMounted(reload)

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
.evo-section {
  margin-bottom: $gap-md;
}
.evo-subtitle {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 $gap-sm;
  display: flex;
  align-items: center;
  gap: 8px;
}
.manifest-box {
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: 10px 12px;
}
.manifest-meta {
  font-size: 12px;
  color: $text-secondary;
}
.manifest-list {
  list-style: none;
  margin: $gap-sm 0 0;
  padding: 0;
  display: grid;
  gap: 4px;
}
.manifest-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.manifest-kind {
  color: $text-primary;
  min-width: 130px;
}
.manifest-count {
  color: $text-secondary;
}
.manifest-hash {
  font-family: 'Roboto Mono', monospace;
  color: $text-muted;
  font-size: 11px;
}
.placeholder-text {
  color: $text-muted;
  font-size: 12px;
  padding: 4px 0;
}
.error-text {
  color: $status-red;
}
</style>
