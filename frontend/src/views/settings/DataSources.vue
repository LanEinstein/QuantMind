<template>
  <div class="data-sources-page">
    <div class="page-actions">
      <el-button type="primary" size="small" @click="onRefreshAll" :loading="refreshing">
        刷新全部
      </el-button>
      <span class="auto-refresh-hint">每60秒自动刷新</span>
    </div>

    <el-card shadow="never">
      <el-table :data="store.dataSources" stripe>
        <el-table-column prop="name" label="数据源" width="160" />
        <el-table-column prop="type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="typeTag(row.type)">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span class="status-cell">
              <span
                class="status-dot"
                :class="row.status === 'connected' ? 'dot-green' : row.status === 'error' ? 'dot-red' : 'dot-gray'"
              />
              {{ statusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="延迟" width="100">
          <template #default="{ row }">
            <span v-if="row.latency_ms > 0">{{ row.latency_ms }}ms</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <span v-if="row.role">{{ row.role === 'primary' ? '主要' : '备用' }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="错误信息" min-width="200">
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="text-muted">无</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              :loading="testingSource === row.name"
              @click="onTestSource(row.name)"
            >
              测试
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'

const store = useSettingsStore()
const refreshing = ref(false)
const testingSource = ref<string | null>(null)

let refreshTimer: ReturnType<typeof setInterval> | null = null

function typeLabel(type: string): string {
  const labels: Record<string, string> = {
    market_data: '行情数据',
    history_data: '历史数据',
    news: '新闻',
    database: '数据库',
    cache: '缓存',
  }
  return labels[type] ?? type
}

function typeTag(type: string): 'success' | 'warning' | 'info' {
  if (type === 'database' || type === 'cache') return 'info'
  if (type === 'market_data') return 'success'
  return 'warning'
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    connected: '已连接',
    configured: '已配置',
    error: '错误',
    unknown: '未知',
  }
  return labels[status] ?? status
}

async function onRefreshAll() {
  refreshing.value = true
  try {
    await store.fetchDataSources()
  } finally {
    refreshing.value = false
  }
}

async function onTestSource(name: string) {
  testingSource.value = name
  try {
    const result = await store.testDataSource(name)
    if (result.status === 'connected') {
      ElMessage.success(`${name} 连接成功`)
    } else {
      ElMessage.error(`${name} 连接失败: ${result.error ?? '未知错误'}`)
    }
  } catch {
    ElMessage.error(`${name} 测试请求失败`)
  } finally {
    testingSource.value = null
  }
}

onMounted(() => {
  store.fetchDataSources()
  refreshTimer = setInterval(() => store.fetchDataSources(), 60_000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style lang="scss" scoped>
.data-sources-page {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.page-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.auto-refresh-hint {
  font-size: 12px;
  color: $text-muted;
}

.status-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;

  &.dot-green { background-color: $status-green; }
  &.dot-red { background-color: $status-red; }
  &.dot-gray { background-color: $text-muted; }
}

.text-muted {
  color: $text-muted;
}

.error-text {
  color: $status-red;
  font-size: 12px;
}
</style>
