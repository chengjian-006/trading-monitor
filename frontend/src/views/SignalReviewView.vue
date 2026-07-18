<script setup lang="ts">
// 信号复盘 (v1.7.662 菜单整合): 买卖胜率(汇总) + 信号历史(明细) 合并为两 Tab。
import TabbedSection from '../components/layout/TabbedSection.vue'
import ReviewView from './ReviewView.vue'
import HistoryView from './HistoryView.vue'

const tabs = [
  { key: 'review', label: '买卖胜率', comp: ReviewView },
  { key: 'history', label: '信号历史', comp: HistoryView },
]
</script>

<template>
  <TabbedSection :tabs="tabs" />
</template>
