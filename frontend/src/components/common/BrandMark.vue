<script setup lang="ts">
// 亮点组件 (v1.7.647): 观潮品牌标记 —— 潮汐波纹 + 水蓝渐变底
// 全站 logo 统一走此件(顶栏/登录页/移动头), 保证品牌一致。
withDefaults(defineProps<{
  size?: number        // 方形边长 px
  radius?: number
  glow?: boolean       // 是否带水蓝辉光
}>(), { size: 24, radius: 7, glow: false })
</script>

<template>
  <span class="brand-mark" :class="{ glow }"
        :style="{ width: size + 'px', height: size + 'px', borderRadius: radius + 'px' }">
    <svg :width="size * 0.58" :height="size * 0.38" viewBox="0 0 26 14" fill="none" aria-hidden="true">
      <path d="M0 7 Q3.25 1 6.5 7 T13 7 T19.5 7 T26 7" stroke="#fff" stroke-width="1.7" stroke-linecap="round" />
      <path d="M0 11 Q3.25 6 6.5 11 T13 11 T19.5 11 T26 11" stroke="#fff" stroke-width="1.3" stroke-linecap="round" opacity="0.55" />
    </svg>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: linear-gradient(145deg, var(--tide-deep), var(--tide-fg));
}
.brand-mark.glow {
  box-shadow: 0 2px 12px rgba(12, 140, 166, 0.35);
}
</style>
