<template>
  <div class="settings-layout">
    <div class="settings-header">
      <h2 class="page-title">系统设置</h2>
    </div>
    <el-tabs
      v-model="activeTab"
      class="settings-tabs"
      @tab-change="onTabChange"
    >
      <el-tab-pane label="LLM路由配置" name="llm-router" />
      <el-tab-pane label="数据源" name="data-sources" />
      <el-tab-pane label="MiroFish配置" name="mirofish" />
      <el-tab-pane label="成本统计" name="cost-dashboard" />
    </el-tabs>
    <div class="settings-content">
      <router-view />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const tabMap: Record<string, string> = {
  'SettingsLLMRouter': 'llm-router',
  'SettingsDataSources': 'data-sources',
  'SettingsMiroFish': 'mirofish',
  'SettingsCostDashboard': 'cost-dashboard',
}

const routeMap: Record<string, string> = {
  'llm-router': '/settings/llm-router',
  'data-sources': '/settings/data-sources',
  'mirofish': '/settings/mirofish',
  'cost-dashboard': '/settings/cost-dashboard',
}

function resolveTab(): string {
  const name = route.name as string
  return tabMap[name] ?? 'llm-router'
}

const activeTab = ref(resolveTab())

watch(
  () => route.name,
  () => { activeTab.value = resolveTab() },
)

function onTabChange(tab: string | number) {
  const path = routeMap[String(tab)]
  if (path && route.path !== path) {
    router.push(path)
  }
}
</script>

<style lang="scss" scoped>
.settings-layout {
  padding: $gap-md $gap-lg;
  min-height: calc(100vh - $status-bar-height);
}

.settings-header {
  margin-bottom: $gap-md;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: $text-primary;
  margin: 0;
}

.settings-tabs {
  margin-bottom: $gap-md;

  :deep(.el-tabs__header) {
    margin-bottom: 0;
  }

  :deep(.el-tabs__item) {
    color: $text-secondary;
    font-size: 14px;

    &.is-active {
      color: $color-accent;
    }
  }
}

.settings-content {
  min-height: 400px;
}
</style>
