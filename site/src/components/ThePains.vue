<script setup lang="ts">
import { PAINS } from '../content'
</script>

<template>
  <section class="sec" id="pains">
    <div class="wrap">
      <p class="sec-tag reveal">01 / 为什么做它</p>
      <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">
        亏钱的原因，很少是「不知道该怎么做」。
      </h2>
      <p class="sec-lede reveal" :style="{ '--d': '120ms' }">
        规则你都懂。问题出在人身上——盯不过来、忍不住、记不清。
      </p>

      <ul class="list">
        <li v-for="(p, i) in PAINS" :key="p.no" class="item reveal" :style="{ '--d': `${180 + i * 90}ms` }">
          <span class="no num">{{ p.no }}</span>
          <div>
            <h3 class="t">{{ p.title }}</h3>
            <p class="b">{{ p.body }}</p>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.list {
  list-style: none;
  margin: 56px 0 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1px;
  background: var(--line-soft); /* 用 gap 露出底色当分隔线 */
  border: 1px solid var(--line-soft);
}

.item {
  background: var(--ink-1);
  padding: 32px 28px;
  display: flex;
  gap: 18px;
  transition: background 0.3s ease;
}

.item:hover {
  background: var(--ink-2);
}

.no {
  font-size: 12px;
  color: var(--amber);
  letter-spacing: 0.1em;
  padding-top: 4px;
  flex: none;
}

.t {
  font-size: 19px;
  margin-bottom: 10px;
}

.b {
  font-size: 14.5px;
  color: var(--fg-dim);
  line-height: 1.8;
}

@media (max-width: 860px) {
  .list {
    grid-template-columns: 1fr;
    margin-top: 40px;
  }
  .item {
    padding: 24px 20px;
  }
}
</style>
