<template>
  <div class="mirofish-config-page">
    <el-card shadow="never">
      <template #header>
        <span class="card-title">MiroFish 仿真参数配置</span>
      </template>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="140px"
        label-position="left"
        class="config-form"
      >
        <el-form-item label="启用仿真" prop="enabled">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <el-form-item label="触发阈值" prop="trigger_threshold">
          <div class="slider-row">
            <el-slider
              v-model="form.trigger_threshold"
              :min="1"
              :max="10"
              :step="1"
              :marks="thresholdMarks"
              show-stops
              class="slider-input"
            />
            <el-input-number
              v-model="form.trigger_threshold"
              :min="1"
              :max="10"
              size="small"
              class="number-input"
            />
          </div>
          <div class="field-hint">DeepSeek初筛重要性评分 ≥ 此值才触发MiroFish仿真</div>
        </el-form-item>

        <el-form-item label="Agent数量" prop="agent_count">
          <div class="slider-row">
            <el-slider
              v-model="form.agent_count"
              :min="100"
              :max="1000"
              :step="50"
              class="slider-input"
            />
            <el-input-number
              v-model="form.agent_count"
              :min="100"
              :max="1000"
              :step="50"
              size="small"
              class="number-input"
            />
          </div>
          <div class="field-hint">模拟市场参与者数量（散户/机构/游资/分析师）</div>
        </el-form-item>

        <el-form-item label="仿真轮次" prop="rounds">
          <div class="slider-row">
            <el-slider
              v-model="form.rounds"
              :min="5"
              :max="50"
              :step="5"
              class="slider-input"
            />
            <el-input-number
              v-model="form.rounds"
              :min="5"
              :max="50"
              :step="5"
              size="small"
              class="number-input"
            />
          </div>
          <div class="field-hint">群体演化仿真轮次</div>
        </el-form-item>

        <el-form-item label="驱动模型" prop="model">
          <el-select v-model="form.model" placeholder="选择模型">
            <el-option label="kimi-k2.6" value="kimi-k2.6" />
            <el-option label="qwen3.6-plus" value="qwen3.6-plus" />
            <el-option label="deepseek-v4-pro" value="deepseek-v4-pro" />
          </el-select>
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            :loading="store.loading"
            @click="onSave"
          >
            保存
          </el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Cost estimate info -->
    <el-card shadow="never" class="estimate-card">
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
import { ref, reactive, computed, onMounted } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'

const store = useSettingsStore()
const formRef = ref<FormInstance>()

const form = reactive({
  enabled: true,
  trigger_threshold: 7,
  agent_count: 300,
  rounds: 20,
  model: 'kimi-k2.6',
})

const rules: FormRules = {
  agent_count: [
    { type: 'number', min: 100, max: 1000, message: 'Agent数量必须在100-1000之间', trigger: 'change' },
  ],
  rounds: [
    { type: 'number', min: 5, max: 50, message: '仿真轮次必须在5-50之间', trigger: 'change' },
  ],
  trigger_threshold: [
    { type: 'number', min: 1, max: 10, message: '触发阈值必须在1-10之间', trigger: 'change' },
  ],
}

const thresholdMarks: Record<number, string> = {
  1: '1', 3: '3', 5: '5', 7: '7', 10: '10',
}

const estimatedTokens = computed(() => {
  // Rough estimate: each agent generates ~2000 tokens per round
  return form.agent_count * form.rounds * 2000
})

const estimatedCost = computed(() => {
  // Kimi K2.6 pricing: input 2.1 + output 8.4 per million tokens
  // Assume ~60% input, ~40% output
  const inputTokens = estimatedTokens.value * 0.6
  const outputTokens = estimatedTokens.value * 0.4
  return (inputTokens * 2.1 + outputTokens * 8.4) / 1_000_000
})

function loadFromStore() {
  const sim = store.mirofishConfig?.simulation
  if (sim) {
    form.enabled = sim.enabled
    form.trigger_threshold = sim.trigger_threshold
    form.agent_count = sim.agent_count
    form.rounds = sim.rounds
    form.model = sim.model
  }
}

async function onSave() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  try {
    await store.updateMiroFishConfig({
      enabled: form.enabled,
      trigger_threshold: form.trigger_threshold,
      agent_count: form.agent_count,
      rounds: form.rounds,
      model: form.model,
    })
    ElMessage.success('MiroFish配置已保存')
  } catch {
    ElMessage.error('保存失败')
  }
}

function onReset() {
  loadFromStore()
  ElMessage.info('已重置为当前配置')
}

onMounted(async () => {
  await store.fetchMiroFishConfig()
  loadFromStore()
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

.config-form {
  max-width: 600px;
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;

  .slider-input {
    flex: 1;
  }

  .number-input {
    width: 120px;
  }
}

.field-hint {
  font-size: 12px;
  color: $text-muted;
  margin-top: 4px;
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
