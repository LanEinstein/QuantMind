<template>
  <div class="mirofish-config-page">
    <el-card shadow="never">
      <template #header>
        <span class="card-title">MiroFish 仿真参数 (只读)</span>
      </template>

      <div class="config-readout" v-if="sim">
        <div class="config-row">
          <span class="config-label">启用仿真</span>
          <span class="config-value">{{ sim.enabled ? '是' : '否' }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">触发阈值</span>
          <span class="config-value">{{ sim.trigger_threshold }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">Agent数量</span>
          <span class="config-value">{{ sim.agent_count }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">仿真轮次</span>
          <span class="config-value">{{ sim.rounds }}</span>
        </div>
        <div class="config-row">
          <span class="config-label">驱动模型</span>
          <span class="config-value">{{ sim.model }}</span>
        </div>
        <div class="config-hint">
          P0-7 / P0-10 红线：MiroFish 配置 runtime 不可改 + hot-reload 已禁用。
          修改需走 git diff + amendment + 重启。
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="estimate-card" v-if="sim">
      <template #header>
        <span class="card-title">成本预估</span>
      </template>
      <div class="estimate-info">
        <div class="estimate-row">
          <span class="estimate-label">每次仿真预估Token</span>
          <span class="estimate-value">
            {{ estimatedTokens.toLocaleString() }} tokens
          </span>
        </div>
        <div class="estimate-row">
          <span class="estimate-label">预估成本</span>
          <span class="estimate-value">
            ¥{{ estimatedCost.toFixed(2) }}
          </span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSettingsStore } from '@/stores/settings'

const store = useSettingsStore()

const sim = computed(() => store.mirofishConfig?.simulation ?? null)

const estimatedTokens = computed(() => {
  if (!sim.value) return 0
  return sim.value.agent_count * sim.value.rounds * 2000
})

const estimatedCost = computed(() => {
  const inputTokens = estimatedTokens.value * 0.6
  const outputTokens = estimatedTokens.value * 0.4
  return (inputTokens * 2.1 + outputTokens * 8.4) / 1_000_000
})

onMounted(async () => {
  await store.fetchMiroFishConfig()
})
</script>

<style lang="scss" scoped>
.mirofish-config-page {
  display: flex;
  flex-direction: column;
  gap: $gap-md;
  max-width: 800px;
}

.card-title {
  font-weight: 600;
  font-size: 14px;
}

.config-readout {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 600px;
}

.config-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.04);
}

.config-label {
  color: $text-muted;
  font-size: 13px;
}

.config-value {
  color: $text-primary;
  font-weight: 600;
  font-family: 'Roboto Mono', monospace;
}

.config-hint {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 11px;
  color: $text-muted;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 4px;
  line-height: 1.5;
}

.estimate-card {
  max-width: 400px;
}

.estimate-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.estimate-row {
  display: flex;
  justify-content: space-between;
  font-size: 14px;

  .estimate-label {
    color: $text-secondary;
  }

  .estimate-value {
    color: $text-primary;
    font-weight: 500;
  }
}
</style>
