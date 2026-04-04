<template>
  <div class="llm-router-page">
    <!-- Provider Cards -->
    <el-row :gutter="12" class="provider-cards">
      <el-col :span="8" v-for="p in store.providerList" :key="p.key">
        <el-card shadow="never" class="provider-card">
          <template #header>
            <div class="provider-header">
              <span class="provider-name">{{ providerLabels[p.key] || p.key }}</span>
              <el-badge
                :type="connectionStatus(p.key)"
                is-dot
                class="status-dot"
              />
            </div>
          </template>
          <div class="provider-body">
            <div class="field-row">
              <span class="field-label">Base URL</span>
              <span class="field-value">{{ p.base_url }}</span>
            </div>
            <div class="field-row">
              <span class="field-label">Model</span>
              <span class="field-value">{{ p.default_model }}</span>
            </div>
            <div class="field-row">
              <span class="field-label">API Key</span>
              <span class="field-value masked">{{ p.api_key }}</span>
            </div>
            <div class="provider-actions">
              <el-button
                type="primary"
                size="small"
                :loading="testingProvider === p.key"
                @click="onTestConnection(p.key)"
              >
                测试连接
              </el-button>
              <span v-if="store.connectionTests[p.key]" class="test-result">
                <template v-if="store.connectionTests[p.key].connected">
                  <el-icon color="#00c853"><CircleCheckFilled /></el-icon>
                  {{ store.connectionTests[p.key].latency_ms }}ms
                </template>
                <template v-else>
                  <el-icon color="#ff1744"><CircleCloseFilled /></el-icon>
                  {{ store.connectionTests[p.key].error }}
                </template>
              </span>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- Expansion slots (grayed out) -->
      <el-col :span="8" v-for="slot in expansionSlots" :key="slot.name">
        <el-card shadow="never" class="provider-card expansion-slot">
          <template #header>
            <div class="provider-header">
              <span class="provider-name muted">{{ slot.label }}</span>
              <el-tag size="small" type="info">待接入</el-tag>
            </div>
          </template>
          <div class="provider-body muted">
            <p>通过YAML配置可零代码接入</p>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Agent-Model Mapping Diagram -->
    <el-card shadow="never" class="mapping-card">
      <template #header>
        <span class="card-title">Agent → 模型映射</span>
      </template>
      <v-chart :option="graphOption" autoresize class="mapping-chart" />
    </el-card>

    <!-- Agent Table -->
    <el-card shadow="never" class="agent-table-card">
      <template #header>
        <div class="table-header">
          <span class="card-title">Agent配置详情</span>
        </div>
      </template>
      <el-table :data="store.agentList" stripe class="agent-table">
        <el-table-column prop="name" label="Agent名称" width="180" />
        <el-table-column prop="provider" label="Provider" width="120">
          <template #default="{ row }">
            <el-tag :type="providerTagType(row.provider)" size="small">
              {{ row.provider }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="160" />
        <el-table-column label="Fallback" width="200">
          <template #default="{ row }">
            <span v-if="row.fallback">
              {{ row.fallback.provider }} / {{ row.fallback.model }}
            </span>
            <span v-else class="text-muted">无</span>
          </template>
        </el-table-column>
        <el-table-column prop="frequency" label="频率" width="140" />
        <el-table-column prop="task" label="任务描述" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { CircleCheckFilled, CircleCloseFilled } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { useSettingsStore } from '@/stores/settings'

const store = useSettingsStore()
const testingProvider = ref<string | null>(null)

const providerLabels: Record<string, string> = {
  deepseek: 'DeepSeek',
  qwen: 'Qwen (DashScope)',
  minimax: 'MiniMax',
}

const providerColors: Record<string, string> = {
  deepseek: '#00c853',
  qwen: '#ff9100',
  minimax: '#448aff',
}

const expansionSlots = [
  { name: 'claude', label: 'Claude (预留)' },
  { name: 'openai', label: 'GPT (预留)' },
]

function connectionStatus(provider: string): 'success' | 'danger' | 'info' {
  const test = store.connectionTests[provider]
  if (!test) return 'info'
  return test.connected ? 'success' : 'danger'
}

function providerTagType(provider: string): 'success' | 'warning' | 'info' {
  if (provider === 'deepseek') return 'success'
  if (provider === 'qwen') return 'warning'
  return 'info'
}

async function onTestConnection(provider: string) {
  testingProvider.value = provider
  try {
    await store.testProvider(provider)
  } finally {
    testingProvider.value = null
  }
}

// ECharts graph option for agent-model mapping
const graphOption = computed(() => {
  const agents = store.agentList
  const providers = store.providerList

  // Build nodes
  const nodes: Array<Record<string, unknown>> = []
  const links: Array<Record<string, unknown>> = []

  // Provider nodes (right side)
  providers.forEach((p, i) => {
    nodes.push({
      name: providerLabels[p.key] || p.key,
      x: 500,
      y: 60 + i * 120,
      symbolSize: 50,
      itemStyle: { color: providerColors[p.key] || '#448aff' },
      category: 0,
    })
  })

  // Agent nodes (left side)
  agents.forEach((a, i) => {
    const color = providerColors[a.provider] || '#448aff'
    nodes.push({
      name: a.name,
      x: 50,
      y: 30 + i * 45,
      symbolSize: 30,
      itemStyle: { color },
      category: 1,
    })

    // Primary link
    const targetName = providerLabels[a.provider] || a.provider
    links.push({
      source: a.name,
      target: targetName,
      lineStyle: { color, width: 2 },
    })

    // Fallback link (dashed)
    if (a.fallback) {
      const fbName = providerLabels[a.fallback.provider] || a.fallback.provider
      links.push({
        source: a.name,
        target: fbName,
        lineStyle: { color: '#6c6c80', width: 1, type: 'dashed' },
      })
    }
  })

  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    series: [{
      type: 'graph',
      layout: 'none',
      data: nodes,
      links,
      roam: false,
      label: {
        show: true,
        position: 'right',
        color: '#e0e0e0',
        fontSize: 11,
      },
      lineStyle: { curveness: 0.2 },
    }],
  }
})

onMounted(() => {
  store.fetchLLMConfig()
})
</script>

<style lang="scss" scoped>
.llm-router-page {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
}

.provider-cards {
  margin-bottom: $gap-sm;
}

.provider-card {
  height: 100%;

  &.expansion-slot {
    opacity: 0.5;
  }
}

.provider-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.provider-name {
  font-weight: 600;
  font-size: 15px;

  &.muted {
    color: $text-muted;
  }
}

.provider-body {
  &.muted {
    color: $text-muted;
    font-size: 13px;
  }
}

.field-row {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;

  .field-label {
    color: $text-muted;
  }

  .field-value {
    color: $text-primary;
    text-align: right;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;

    &.masked {
      color: $text-muted;
      font-style: italic;
    }
  }
}

.provider-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.test-result {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: $text-secondary;
}

.mapping-card {
  .mapping-chart {
    height: 500px;
    width: 100%;
  }
}

.agent-table-card {
  .table-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
}

.card-title {
  font-weight: 600;
  font-size: 14px;
}

.text-muted {
  color: $text-muted;
}
</style>
