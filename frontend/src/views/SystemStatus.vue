<template>
  <section class="placeholder-shell">
    <h2 class="placeholder-title">系统状态</h2>
    <p class="placeholder-body">
      5 冻结源监控面板由 G-002 落地。当前页为 G-001 应用壳占位,导航可达即可。
    </p>
  </section>
</template>

<script setup lang="ts">
// G-001 placeholder. G-002 (StatusBar 5 freeze sources) replaces this view.
</script>

<style lang="scss" scoped>
.placeholder-shell {
  padding: $gap-lg;
  color: $text-secondary;
}
.placeholder-title {
  font-size: 18px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 $gap-md;
}
.placeholder-body {
  font-size: 13px;
}
</style>
