<script setup lang="ts">
import { ref } from 'vue'
import { FAQS } from '../content'

// 手风琴: 默认展开第一条, 其余点开。同时只开一条, 免得整页变成一堵墙。
const open = ref(0)
const toggle = (i: number) => (open.value = open.value === i ? -1 : i)
</script>

<template>
  <section class="sec" id="faq">
    <div class="wrap">
      <p class="sec-tag reveal">06 / 常见问题</p>
      <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">先把话说清楚</h2>

      <ul class="qs">
        <li v-for="(f, i) in FAQS" :key="i" class="q reveal" :style="{ '--d': `${120 + i * 60}ms` }">
          <button class="q-h" :aria-expanded="open === i" @click="toggle(i)">
            <span class="q-t">{{ f.q }}</span>
            <span class="q-i" :class="{ on: open === i }" aria-hidden="true"></span>
          </button>

          <div class="q-b" :class="{ on: open === i }">
            <p>{{ f.a }}</p>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.qs {
  list-style: none;
  margin: 48px 0 0;
  padding: 0;
  border-top: 1px solid var(--line-soft);
}

.q {
  border-bottom: 1px solid var(--line-soft);
}

.q-h {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 0;
  background: none;
  border: 0;
  cursor: pointer;
  text-align: left;
  color: var(--fg-strong);
  font-family: var(--font-serif);
  font-size: clamp(16px, 2.1vw, 19px);
  font-weight: 600;
  transition: color 0.2s ease;
}

.q-h:hover {
  color: var(--amber);
}

/* 加号 → 减号 */
.q-i {
  position: relative;
  width: 13px;
  height: 13px;
  flex: none;
}

.q-i::before,
.q-i::after {
  content: '';
  position: absolute;
  background: var(--amber);
  transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.q-i::before {
  left: 0;
  top: 6px;
  width: 13px;
  height: 1px;
}

.q-i::after {
  left: 6px;
  top: 0;
  width: 1px;
  height: 13px;
}

.q-i.on::after {
  transform: scaleY(0);
}

.q-b {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.q-b.on {
  grid-template-rows: 1fr;
}

.q-b > p {
  overflow: hidden;
  font-size: 14.5px;
  line-height: 1.9;
  color: var(--fg-dim);
  max-width: 74ch;
}

.q-b.on > p {
  padding-bottom: 24px;
}

@media (max-width: 768px) {
  .qs {
    margin-top: 36px;
  }
  .q-h {
    padding: 18px 0;
  }
}
</style>
