<script setup lang="ts">
// 个股行情整页(/intraday): 分时 + 日K + 大单。主体复用 StockCharts, 与股票池弹框同源。
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import StockCharts from '../components/chart/StockCharts.vue'

const route = useRoute()
const code = ref((route.query.code as string) || '')
const name = ref((route.query.name as string) || '')
</script>

<template>
  <div class="intraday-page">
    <div class="page-header">
      <span class="page-title">{{ name || code }} <span class="code">{{ code }}</span> · 行情</span>
    </div>
    <div v-if="!code" class="empty">缺少股票代码，链接应形如 /intraday?code=600519</div>
    <StockCharts v-else :code="code" :name="name" />
  </div>
</template>

<style scoped>
.intraday-page {
  max-width: 720px;
  margin: 0 auto;
  padding: 12px;
}
.page-header {
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text1);
}
.page-title .code {
  font-size: 13px;
  color: var(--text2);
  font-family: monospace;
  font-variant-numeric: tabular-nums;
}
.empty {
  text-align: center;
  padding: 60px 0;
  color: var(--text2);
}
</style>
