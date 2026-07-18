<script setup lang="ts">
// 系统设置 (v1.7.662 菜单整合): 系统设置 + 定时任务 + 推送模版 合并为三 Tab(均后台配置)。
import TabbedSection from '../components/layout/TabbedSection.vue'
import ConfigView from './ConfigView.vue'
import ScheduledTaskView from './ScheduledTaskView.vue'
import LarkTemplateView from './LarkTemplateView.vue'

const tabs = [
  { key: 'config', label: '系统设置', comp: ConfigView },
  { key: 'scheduled-tasks', label: '定时任务', comp: ScheduledTaskView },
  { key: 'lark-templates', label: '推送模版', comp: LarkTemplateView },
]
</script>

<template>
  <TabbedSection :tabs="tabs" />
</template>
