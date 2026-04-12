<template>
  <v-chart
    class="mini-donut"
    :option="chartOption"
    :autoresize="false"
    style="width: 36px; height: 36px;"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type { PieSeriesOption } from 'echarts/charts'

use([CanvasRenderer, PieChart])

type ChartOption = ComposeOption<PieSeriesOption>

interface Props {
  data: Readonly<Record<string, number>>
}

const props = defineProps<Props>()

const chartOption = computed((): ChartOption => ({
  animation: false,
  backgroundColor: 'transparent',
  series: [
    {
      type: 'pie',
      radius: ['55%', '90%'],
      center: ['50%', '50%'],
      silent: true,
      label: { show: false },
      labelLine: { show: false },
      data: [
        {
          name: 'bullish',
          value: props.data['bullish'] ?? 0,
          itemStyle: { color: '#ff1744' },
        },
        {
          name: 'bearish',
          value: props.data['bearish'] ?? 0,
          itemStyle: { color: '#00c853' },
        },
        {
          name: 'neutral',
          value: props.data['neutral'] ?? 0,
          itemStyle: { color: '#ffd600' },
        },
      ],
    },
  ],
}))
</script>

<style scoped>
.mini-donut {
  display: inline-block;
  vertical-align: middle;
}
</style>
