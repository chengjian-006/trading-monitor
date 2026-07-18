<script setup lang="ts">
// 可复用查询区容器 (v1.7.667): 桌面照常展开; 手机端(<768)默认折叠, 顶部「筛选」开关点开——
// 避免查询表单在手机上占满首屏把内容顶到看不见。各带 filter-bar 的页把筛选区包进本组件即可。
import { ref } from 'vue'
import { NIcon } from 'naive-ui'
import { FunnelOutline, ChevronDownOutline } from '@vicons/ionicons5'
import { useResponsive } from '../../composables/useResponsive'

withDefaults(defineProps<{ label?: string }>(), { label: '筛选' })
const { isPhone } = useResponsive()
const open = ref(false)
</script>

<template>
  <div class="filter-panel">
    <button v-if="isPhone" type="button" class="fp-toggle" :class="{ open }"
            :aria-expanded="open" @click="open = !open">
      <NIcon :component="FunnelOutline" :size="15" />
      <span class="fp-label">{{ label }}</span>
      <span class="fp-hint">{{ open ? '收起' : '展开' }}</span>
      <NIcon class="fp-chev" :component="ChevronDownOutline" :size="16" />
    </button>
    <div v-show="!isPhone || open" class="fp-body">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.fp-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 42px;
  padding: 0 14px;
  margin-bottom: 10px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--bg-surface);
  color: var(--fg-default);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  touch-action: manipulation;
}
.fp-toggle .fp-hint {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--fg-subtle);
}
.fp-chev {
  color: var(--fg-subtle);
  transition: transform 0.2s;
}
.fp-toggle.open .fp-chev { transform: rotate(180deg); }
</style>
