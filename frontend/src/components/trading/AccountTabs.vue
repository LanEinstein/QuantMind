<template>
  <div class="account-tabs">
    <el-tabs v-model="activeTab" type="card" @tab-change="onTabChange">
      <el-tab-pane
        v-for="acct in store.accounts"
        :key="acct.account_id"
        :label="acct.label"
        :name="acct.account_id"
      />
    </el-tabs>
    <el-button class="add-btn" size="small" text :icon="Plus" @click="onAdd">
      新建
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePortfolioStore } from '@/stores/portfolio'

const store = usePortfolioStore()

const activeTab = computed({
  get: () => store.activeAccountId,
  set: (val: string) => {
    store.activeAccountId = val
  },
})

function onTabChange(accountId: string | number) {
  store.switchAccount(String(accountId))
}

function onAdd() {
  ElMessage.info('多策略账户功能将在Phase 5中开放')
}
</script>

<style scoped lang="scss">
.account-tabs {
  display: flex;
  align-items: center;
  gap: $gap-sm;
  margin-bottom: $gap-md;

  :deep(.el-tabs) {
    flex: 1;
  }

  :deep(.el-tabs__header) {
    margin-bottom: 0;
  }
}

.add-btn {
  flex-shrink: 0;
}
</style>
