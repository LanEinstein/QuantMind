<template>
  <AppShell>
    <router-view v-slot="{ Component }">
      <transition name="fade" mode="out-in">
        <keep-alive :include="['Dashboard']">
          <component :is="Component" />
        </keep-alive>
      </transition>
    </router-view>
  </AppShell>
</template>

<script setup lang="ts">
import AppShell from '@/components/layout/AppShell.vue'
</script>

<style lang="scss">
#app {
  min-height: 100vh;
  background-color: $bg-primary;
}
</style>
