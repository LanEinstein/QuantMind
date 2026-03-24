<template>
  <router-view v-slot="{ Component }">
    <transition name="fade" mode="out-in">
      <keep-alive :include="['Dashboard']">
        <component :is="Component" />
      </keep-alive>
    </transition>
  </router-view>
</template>

<script setup lang="ts">
// App root — dark theme applied in main.ts
</script>

<style lang="scss">
#app {
  min-height: 100vh;
  background-color: $bg-primary;
}
</style>
