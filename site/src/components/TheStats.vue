<script setup lang="ts">
import { STATS } from '../content'
</script>

<template>
  <section class="stats" aria-label="系统规模">
    <div class="wrap grid">
      <div v-for="(s, i) in STATS" :key="s.label" class="cell reveal" :style="{ '--d': `${i * 60}ms` }">
        <p class="v num">
          {{ s.value }}<span class="u">{{ s.unit }}</span>
        </p>
        <p class="l">{{ s.label }}</p>
        <p class="n">{{ s.note }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stats {
  border-top: 1px solid var(--line-soft);
  border-bottom: 1px solid var(--line-soft);
  background: rgba(8, 9, 12, 0.55);
  padding: 0;
}

.grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
}

.cell {
  padding: 34px 20px;
  border-left: 1px solid var(--line-soft);
}

.cell:first-child {
  border-left: 0;
}

.v {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: clamp(24px, 3vw, 32px);
  font-weight: 700;
  color: var(--fg-strong);
  line-height: 1.1;
  letter-spacing: -0.01em;
}

.u {
  font-size: 13px;
  font-weight: 400;
  color: var(--amber);
  margin-left: 4px;
  letter-spacing: 0;
}

.l {
  font-size: 13px;
  color: var(--fg);
  margin-top: 10px;
}

.n {
  font-size: 12px;
  color: var(--fg-faint);
  line-height: 1.55;
  margin-top: 4px;
}

/* 平板两行三列, 手机两列 —— 6 个数字在窄屏排一行会挤成条形码 */
@media (max-width: 1000px) {
  .grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .cell:nth-child(3n + 1) {
    border-left: 0;
  }
  .cell:nth-child(n + 4) {
    border-top: 1px solid var(--line-soft);
  }
}

@media (max-width: 620px) {
  .grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .cell {
    padding: 24px 16px;
  }
  .cell:nth-child(n) {
    border-left: 1px solid var(--line-soft);
    border-top: 1px solid var(--line-soft);
  }
  .cell:nth-child(odd) {
    border-left: 0;
  }
  .cell:nth-child(-n + 2) {
    border-top: 0;
  }
}
</style>
