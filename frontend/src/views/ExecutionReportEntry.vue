<template>
  <section class="execution-report-entry">
    <header class="page-header">
      <div>
        <h2 class="page-title">用户回报录入(备用通道)</h2>
        <p class="page-subtitle">
          P0-4 §1.1 备用通道 — 与飞书主通道共用同一后端 parser。提交前请先确认
          预览区显示 ✓ 通过。预览仅为前端便捷校验,<strong>后端 parser 为最终权威</strong>;
          JS 正则镜像与后端 <code>PATTERNS_AS_DICT</code> 由单元测试断言逐字节相等。
        </p>
      </div>
    </header>

    <article class="template-card">
      <header class="template-header">
        <span class="template-label">回报模板(5 选 1)</span>
      </header>
      <div class="template-buttons">
        <el-button
          v-for="tpl in templates"
          :key="tpl.id"
          size="small"
          :type="selectedTemplate === tpl.id ? 'primary' : 'default'"
          @click="applyTemplate(tpl)"
        >
          {{ tpl.label }}
        </el-button>
      </div>
      <p v-if="selectedTemplate" class="template-desc">
        {{ selectedTemplateDescription }}
      </p>
    </article>

    <article class="entry-card">
      <header class="entry-header">
        <span class="entry-label">回报原文</span>
        <span class="entry-counter">{{ rawText.length }} / 4096</span>
      </header>
      <el-input
        v-model="rawText"
        type="textarea"
        :rows="6"
        placeholder="请粘贴或填写一条用户回报。点击模板按钮可插入占位符。"
        :maxlength="4096"
        show-word-limit
      />
    </article>

    <article :class="['preview-card', previewClass]">
      <header class="preview-header">
        <span class="preview-status">{{ previewStatusLabel }}</span>
        <span v-if="preview.patternId" class="preview-pattern">
          {{ preview.patternId }}
        </span>
      </header>
      <div v-if="preview.patternId" class="preview-groups">
        <div v-for="(value, key) in preview.groups" :key="key" class="kv-row">
          <span>{{ key }}</span>
          <span>{{ value }}</span>
        </div>
      </div>
      <div v-else-if="rawText" class="preview-empty">
        当前文本不匹配任何已锁定模板。请检查 instruction_id 格式、空格、全角/半角字符。
      </div>
      <div v-else class="preview-empty">输入回报后,这里会显示解析预览。</div>
    </article>

    <article class="submit-card">
      <el-button
        type="primary"
        :disabled="!preview.patternId || submitting"
        :loading="submitting"
        @click="submit"
      >
        提交回报
      </el-button>
      <span v-if="!preview.patternId && rawText" class="submit-hint">
        预览未通过,无法提交。
      </span>
    </article>

    <article v-if="result" :class="['result-card', resultClass]">
      <header class="result-header">
        <span class="result-title">{{ resultTitle }}</span>
        <span v-if="result.instruction_id" class="result-iid">
          {{ result.instruction_id }}
        </span>
      </header>
      <div v-if="result.success && result.apply_result" class="result-grid">
        <div class="kv-row"><span>现金 delta</span><span>{{ result.apply_result.cash_delta }}</span></div>
        <div class="kv-row"><span>broker_event_sequence</span><span>{{ result.apply_result.broker_event_sequence ?? '—' }}</span></div>
        <div class="kv-row"><span>reason</span><span>{{ result.apply_result.reason }}</span></div>
        <div class="kv-row"><span>positions_delta</span><span>{{ positionsDeltaSummary }}</span></div>
      </div>
      <div v-else-if="result.ambiguous" class="result-clarification">
        后端无法唯一解析这条回报。澄清模板 ID:
        <code>{{ result.template_id || 'NO_PATTERN_MATCH' }}</code>。
        请核对回报内容并按澄清提示修正后重新提交。
      </div>
    </article>

    <article v-if="submitError" class="banner banner-error">
      提交失败:{{ submitError }}
    </article>

    <article class="mirror-card">
      <header class="mirror-header">JS 正则镜像</header>
      <p class="mirror-text">
        本页正则镜像 <code>backend/execution/regex_patterns.py</code> 的
        <code>PATTERNS_AS_DICT</code>(单一真相源)。后端生成 normalized 工件
        (<code>(?P&lt;…&gt;)</code>→<code>(?&lt;…&gt;)</code>),后端测试断言工件 == SSoT、
        vitest 断言本页 <code>PATTERN_STRINGS</code> == 工件,二者逐字节相等;任何一边
        单方修改都会让其中一个测试失败(P0-4 §1.1)。
      </p>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import {
  TEMPLATES,
  type PreviewMatch,
  previewExecutionReport,
} from '@/utils/executionRegex'
import { executionReportsApi, type ExecutionReportSubmitOutcome } from '@/api/executionReports'

const rawText = ref('')
const selectedTemplate = ref<string | null>(null)
const submitting = ref(false)
const submitError = ref<string | null>(null)
const result = ref<ExecutionReportSubmitOutcome | null>(null)
const preview = ref<PreviewMatch>({ patternId: null, groups: {} })

const templates = TEMPLATES

const selectedTemplateDescription = computed(() => {
  const tpl = templates.find((t) => t.id === selectedTemplate.value)
  return tpl?.description ?? ''
})

const previewClass = computed(() =>
  preview.value.patternId ? 'preview-pass' : rawText.value ? 'preview-fail' : 'preview-empty-state',
)

const previewStatusLabel = computed(() => {
  if (preview.value.patternId) return '✓ 预览通过'
  if (rawText.value) return '✗ 预览未通过'
  return '尚未输入'
})

const resultClass = computed(() =>
  result.value?.success ? 'result-pass' : result.value?.ambiguous ? 'result-clarify' : 'result-neutral',
)

const resultTitle = computed(() => {
  if (!result.value) return ''
  if (result.value.success) return '✓ 已应用到 MockBroker'
  if (result.value.ambiguous) return '⚠ 需澄清'
  return '提交完成'
})

const positionsDeltaSummary = computed(() => {
  const apply = result.value?.apply_result
  if (!apply || !apply.positions_delta || apply.positions_delta.length === 0) {
    return '—'
  }
  return apply.positions_delta
    .map((p) => `${p.code ?? '?'}:${p.volume ?? p.volume_delta ?? '?'}`)
    .join(', ')
})

watch(rawText, (next) => {
  preview.value = previewExecutionReport(next)
})

function applyTemplate(tpl: (typeof templates)[number]) {
  selectedTemplate.value = tpl.id
  rawText.value = tpl.placeholder
}

async function submit() {
  if (!preview.value.patternId) return
  submitting.value = true
  submitError.value = null
  result.value = null
  try {
    result.value = await executionReportsApi.submit(rawText.value)
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.execution-report-entry {
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

.template-card,
.entry-card,
.preview-card,
.submit-card,
.result-card,
.mirror-card {
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 12px 16px;
}

.template-buttons {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.template-desc {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.entry-header,
.template-header,
.preview-header,
.result-header,
.mirror-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.entry-counter {
  color: var(--el-text-color-secondary);
  font-weight: 400;
  font-size: 12px;
}

.preview-card.preview-pass {
  border-color: var(--el-color-success);
}

.preview-card.preview-fail {
  border-color: var(--el-color-warning);
}

.preview-card.preview-empty-state {
  border-style: dashed;
}

.preview-status {
  font-size: 14px;
}

.preview-pattern {
  font-family: monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.preview-groups {
  margin-top: 8px;
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

.preview-empty {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.submit-card {
  display: flex;
  align-items: center;
  gap: 12px;
}

.submit-hint {
  color: var(--el-color-warning);
  font-size: 13px;
}

.result-pass {
  border-color: var(--el-color-success);
}

.result-clarify {
  border-color: var(--el-color-warning);
}

.result-neutral {
  border-color: var(--el-border-color-light);
}

.result-grid {
  margin-top: 8px;
}

.result-clarification {
  margin-top: 8px;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.result-iid {
  font-family: monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
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

.mirror-card {
  background: transparent;
  border-style: dashed;
}

.mirror-text {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.mirror-text code {
  font-family: monospace;
  background: var(--el-fill-color-light);
  padding: 0 4px;
  border-radius: 3px;
}
</style>
