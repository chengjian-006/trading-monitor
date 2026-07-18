<script setup lang="ts">
// 通用 Tab 聚合容器 (v1.7.662): 把多个原独立页面当组件聚合成一个带 Tab 的页面,
// 子页面零改动、原样渲染, KeepAlive 缓存避免切 Tab 重载数据。菜单整合用。
import { ref, markRaw, type Component } from 'vue'

const props = defineProps<{
  tabs: { key: string; label: string; comp: Component }[]
  initial?: string
}>()

const rawTabs = props.tabs.map((t) => ({ ...t, comp: markRaw(t.comp) }))
const active = ref(props.initial || rawTabs[0]?.key)
</script>

<template>
  <div class="tabbed-section">
    <div class="ts-bar" role="tablist">
      <button v-for="t in rawTabs" :key="t.key" role="tab"
              :aria-selected="active === t.key"
              :class="['ts-tab', { on: active === t.key }]"
              @click="active = t.key">
        {{ t.label }}
      </button>
    </div>
    <div class="ts-body">
      <KeepAlive>
        <component :is="rawTabs.find((t) => t.key === active)?.comp" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
.ts-bar {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--border-default);
  margin-bottom: 12px;
  overflow-x: auto;
}
.ts-tab {
  appearance: none;
  border: 0;
  background: transparent;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg-muted);
  padding: 9px 16px;
  cursor: pointer;
  white-space: nowrap;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color .15s, border-color .15s;
}
.ts-tab:hover { color: var(--fg-default); }
.ts-tab.on {
  color: var(--accent-fg);
  border-bottom-color: var(--accent-fg);
}
</style>
