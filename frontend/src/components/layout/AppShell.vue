<template>
  <div class="app-shell">
    <aside class="app-sidebar" :class="{ collapsed: focusMode }">
      <header class="app-brand">
        <span class="brand-mark">QM</span>
        <span v-show="!focusMode" class="brand-name">QuantMind</span>
      </header>
      <el-menu
        :default-active="activeRoute"
        :collapse="focusMode"
        :collapse-transition="false"
        :unique-opened="false"
        class="shell-menu"
        background-color="transparent"
        text-color="#a0a0b0"
        active-text-color="#82b1ff"
        router
      >
        <el-menu-item-group
          v-for="group in NAV_GROUPS"
          :key="group.id"
          :title="group.title"
        >
          <el-menu-item
            v-for="entry in group.entries"
            :key="entry.path"
            :index="entry.path"
          >
            <span class="entry-label">{{ entry.title }}</span>
          </el-menu-item>
        </el-menu-item-group>

        <el-sub-menu index="settings">
          <template #title>
            <span class="entry-label settings-label">设置(只读)</span>
          </template>
          <el-menu-item
            v-for="entry in SETTINGS_ENTRIES"
            :key="entry.path"
            :index="entry.path"
          >
            {{ entry.title }}
          </el-menu-item>
        </el-sub-menu>
      </el-menu>

      <footer class="app-sidebar-footer">
        <el-button
          size="small"
          text
          @click="toggleFocusMode"
        >
          {{ focusMode ? '展开' : '聚焦模式' }}
        </el-button>
      </footer>
    </aside>

    <main class="app-main">
      <div class="app-content">
        <slot />
      </div>
      <footer class="app-status-bar">
        <FreezeSourceBar :sources="systemStatusStore.sources" />
        <span class="status-spacer" />
        <span class="status-time">
          {{ statusBarTimestamp }}
        </span>
      </footer>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { NAV_GROUPS, SETTINGS_ENTRIES } from '@/router/menu'
import { useFocusMode } from '@/composables/useFocusMode'
import { useSystemStatusStore } from '@/stores/systemStatus'
import FreezeSourceBar from '@/components/common/FreezeSourceBar.vue'

const route = useRoute()
const { active: focusMode, toggle: toggleFocusMode } = useFocusMode()

const activeRoute = computed<string>(() => route.path)

const systemStatusStore = useSystemStatusStore()
const { timestamp } = storeToRefs(systemStatusStore)

const statusBarTimestamp = computed(() => {
  if (!timestamp.value) return '加载中…'
  try {
    return new Date(timestamp.value).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return timestamp.value
  }
})

const POLL_INTERVAL_MS = 10_000
let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  systemStatusStore.fetchFreezeSources()
  pollTimer = setInterval(() => {
    systemStatusStore.fetchFreezeSources()
  }, POLL_INTERVAL_MS)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style lang="scss" scoped>
.app-shell {
  display: flex;
  height: 100vh;
  background: $bg-primary;
}

.app-sidebar {
  width: 220px;
  flex-shrink: 0;
  background: $bg-sidebar;
  border-right: 1px solid $border-color;
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
  height: 100vh;

  &.collapsed {
    width: 60px;
  }
}

.app-brand {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  border-bottom: 1px solid $border-color;
}

.brand-mark {
  display: inline-flex;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: $color-accent;
  color: #fff;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.5px;
}

.brand-name {
  color: $text-primary;
  font-weight: 600;
  font-size: 14px;
}

.shell-menu {
  flex: 1;
  overflow-y: auto;
  border-right: none;

  :deep(.el-menu-item-group__title) {
    color: $text-muted;
    font-size: 11px;
    letter-spacing: 0.5px;
    padding: 12px 18px 4px;
    text-transform: none;
  }

  :deep(.el-menu-item) {
    height: 36px;
    line-height: 36px;
    font-size: 13px;
    padding-left: 24px !important;

    &.is-active {
      background: rgba(68, 138, 255, 0.12);
    }
  }

  :deep(.el-sub-menu__title) {
    height: 36px;
    line-height: 36px;
    font-size: 13px;
    padding-left: 18px !important;
  }
}

.entry-label {
  font-size: 13px;
}

.settings-label {
  color: $text-muted;
}

.app-sidebar-footer {
  padding: 8px 12px;
  border-top: 1px solid $border-color;
  text-align: center;
}

.app-main {
  flex: 1;
  min-width: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: $bg-primary;
}

.app-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.app-status-bar {
  height: $status-bar-height;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  background: $bg-status-bar;
  border-top: 1px solid $border-color;
  font-size: 12px;
}

.status-spacer {
  flex: 1;
}

.status-time {
  color: $text-muted;
  font-family: 'Roboto Mono', monospace;
}
</style>
