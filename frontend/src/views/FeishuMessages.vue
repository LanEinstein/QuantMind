<template>
  <section class="feishu-messages">
    <header class="page-header">
      <div>
        <h2 class="page-title">飞书消息历史(只读)</h2>
        <p class="page-subtitle">
          P0-2 / F-003 / F-006 — 飞书 inbound / outbound 全部走 audit_events
          双写;本页从 audit (Mongo 优先,JSONL 兜底)读取最近事件,凭证
          均以 SHA256[:8] fingerprint 形式显示,严禁明文(P1-6 §1.2)。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
    </header>

    <article v-if="error" class="banner banner-error">加载失败:{{ error }}</article>

    <article v-if="payload?.source === 'jsonl_fallback'" class="banner banner-info">
      Mongo 暂时不可达,正在从 logs/audit.jsonl 读取最近 {{ payload.count }} 条历史。
    </article>

    <el-table :data="rows" stripe size="small">
      <el-table-column prop="timestamp" label="时间" width="200">
        <template #default="{ row }">{{ formatTime(row.timestamp) }}</template>
      </el-table-column>
      <el-table-column prop="event_type" label="事件" width="220">
        <template #default="{ row }">
          <span :class="['pill', pillClass(row.event_type)]">{{ row.event_type }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="actor" label="actor" width="140" />
      <el-table-column prop="outcome" label="outcome" width="120" />
      <el-table-column prop="resource_id" label="resource_id" min-width="180" />
      <el-table-column prop="correlation_id" label="correlation_id" min-width="180" />
      <el-table-column label="payload" min-width="240">
        <template #default="{ row }">
          <code class="payload-cell">{{ formatPayload(row.payload) }}</code>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElTable, ElTableColumn } from 'element-plus'
import {
  feishuMessagesApi,
  type FeishuMessagesPayload,
} from '@/api/feishuMessages'

const loading = ref(false)
const error = ref<string | null>(null)
const payload = ref<FeishuMessagesPayload | null>(null)

const rows = computed(() => payload.value?.events ?? [])

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString()
}

function formatPayload(p: Record<string, unknown>): string {
  if (!p || Object.keys(p).length === 0) return '—'
  return Object.entries(p)
    .map(([key, value]) => {
      const text =
        typeof value === 'string' && value.length > 80
          ? `${value.slice(0, 80)}…`
          : JSON.stringify(value)
      return `${key}=${text}`
    })
    .join(' ')
}

function pillClass(eventType: string): string {
  if (eventType.endsWith('_RECEIVED') || eventType.endsWith('_CONNECTED')) {
    return 'pill-info'
  }
  if (eventType.endsWith('_DISCONNECTED')) return 'pill-warning'
  return 'pill-success'
}

async function refresh() {
  loading.value = true
  error.value = null
  try {
    payload.value = await feishuMessagesApi.list(100)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
.feishu-messages {
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

.banner-error {
  background: var(--el-color-danger-light-9);
  border-left: 4px solid var(--el-color-danger);
  color: var(--el-color-danger);
}

.banner-info {
  background: var(--el-color-info-light-9);
  border-left: 4px solid var(--el-color-info);
  color: var(--el-color-info);
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

.payload-cell {
  font-family: monospace;
  font-size: 11px;
  word-break: break-all;
}
</style>
