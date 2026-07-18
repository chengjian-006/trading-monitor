<script setup lang="ts">
// 模型 · 策略 (v1.7.662 菜单整合): 模型图鉴(看模型+胜率) + 策略配置(调参数) 合并为两 Tab。
import TabbedSection from '../components/layout/TabbedSection.vue'
import ModelsView from './ModelsView.vue'
import SignalConfigView from './SignalConfigView.vue'

const tabs = [
  { key: 'models', label: '模型图鉴', comp: ModelsView },
  { key: 'signal-config', label: '策略配置', comp: SignalConfigView },
]
</script>

<template>
  <TabbedSection :tabs="tabs" />
</template>
