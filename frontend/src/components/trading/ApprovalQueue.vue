<template>
  <div v-if="approvals.length > 0" class="approval-queue">
    <div class="queue-header">
      <span class="queue-title">待审批订单队列</span>
      <el-badge :value="approvals.length" type="warning" />
    </div>

    <div class="queue-list">
      <div v-for="item in approvals" :key="item.id" class="approval-card">
        <div class="approval-main">
          <div class="recommendation">{{ item.agent_recommendation }}</div>

          <el-collapse class="reasoning-collapse">
            <el-collapse-item title="查看分析理由">
              <p class="reasoning-text">{{ item.reasoning }}</p>
            </el-collapse-item>
          </el-collapse>

          <div class="risk-check" :class="item.risk_pre_check.passed ? 'check-pass' : 'check-warn'">
            <span v-if="item.risk_pre_check.passed" class="check-icon pass-icon">&#x2705;</span>
            <span v-else class="check-icon warn-icon">&#x26A0;&#xFE0F;</span>
            {{ item.risk_pre_check.message || (item.risk_pre_check.passed ? '风控检查: 通过' : '风控检查: 警告') }}
          </div>
        </div>

        <div class="approval-actions">
          <el-button type="success" size="small" @click="onApprove(item.id)">
            批准
          </el-button>
          <el-button type="danger" size="small" @click="onReject(item.id)">
            拒绝
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ElMessageBox } from 'element-plus'
import type { PendingApproval } from '@/types/trading'

defineProps<{
  approvals: readonly PendingApproval[]
}>()

const emit = defineEmits<{
  approve: [id: string]
  reject: [id: string]
}>()

async function onApprove(id: string) {
  try {
    await ElMessageBox.confirm('确认批准该订单？', '批准确认', {
      confirmButtonText: '批准',
      cancelButtonText: '取消',
      type: 'success',
    })
    emit('approve', id)
  } catch {
    // User cancelled
  }
}

async function onReject(id: string) {
  try {
    await ElMessageBox.confirm('确认拒绝该订单？', '拒绝确认', {
      confirmButtonText: '拒绝',
      cancelButtonText: '取消',
      type: 'warning',
    })
    emit('reject', id)
  } catch {
    // User cancelled
  }
}
</script>

<style scoped lang="scss">
.approval-queue {
  border: 1px solid $status-yellow;
  border-radius: $border-radius;
  background: rgba($status-yellow, 0.04);
  padding: $gap-md;
  margin-bottom: $gap-md;
}

.queue-header {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  margin-bottom: $gap-md;
}

.queue-title {
  font-size: 16px;
  font-weight: 600;
  color: $status-yellow;
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: $gap-sm;
}

.approval-card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $gap-md;
  background: $bg-card;
  border: 1px solid $border-color;
  border-radius: $border-radius;
  padding: $gap-md;
}

.approval-main {
  flex: 1;
}

.recommendation {
  font-weight: 600;
  color: $text-primary;
  margin-bottom: 8px;
}

.reasoning-collapse {
  margin-bottom: 8px;
}

.reasoning-text {
  color: $text-secondary;
  font-size: 13px;
  line-height: 1.6;
  margin: 0;
}

.risk-check {
  font-size: 13px;
  padding: 4px 8px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.check-pass {
  background: rgba($status-green, 0.1);
  color: $status-green;
}

.check-warn {
  background: rgba($status-yellow, 0.1);
  color: $status-yellow;
}

.check-icon {
  font-size: 14px;
}

.approval-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}
</style>
