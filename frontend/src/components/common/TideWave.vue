<script setup lang="ts">
// 亮点组件 (v1.7.647): 观潮潮汐波纹 motif —— 品牌视觉语言的基元
// 用于 logo/区块分隔/空态/加载。双层波纹 = 观潮; 颜色默认潮汐青, 可覆盖。
withDefaults(defineProps<{
  width?: number
  height?: number
  color?: string       // 主波纹色, 默认潮汐青
  layers?: 1 | 2        // 1=单波纹(分隔), 2=双波纹(logo/空态)
  strokeWidth?: number
}>(), { width: 34, height: 12, color: 'var(--tide-fg)', layers: 2, strokeWidth: 1.5 })
</script>

<template>
  <svg :width="width" :height="height" :viewBox="`0 0 34 12`" fill="none"
       preserveAspectRatio="none" class="tide-wave" aria-hidden="true">
    <path d="M0 6 Q4.25 1 8.5 6 T17 6 T25.5 6 T34 6"
          :stroke="color" :stroke-width="strokeWidth" stroke-linecap="round" />
    <path v-if="layers === 2" d="M0 10 Q4.25 6 8.5 10 T17 10 T25.5 10 T34 10"
          :stroke="color" :stroke-width="strokeWidth * 0.85" stroke-linecap="round" opacity="0.5" />
  </svg>
</template>

<style scoped>
.tide-wave { display: block; flex-shrink: 0; }
</style>
