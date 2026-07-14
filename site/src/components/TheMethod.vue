<script setup lang="ts">
import { METHOD } from '../content'
</script>

<template>
  <section class="sec" id="method">
    <div class="wrap">
      <p class="sec-tag reveal">03 / 方法</p>
      <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">{{ METHOD.title }}</h2>
      <p class="sec-lede reveal" :style="{ '--d': '120ms' }">{{ METHOD.lede }}</p>

      <ol class="steps">
        <li v-for="(s, i) in METHOD.steps" :key="s.no" class="step reveal" :style="{ '--d': `${180 + i * 90}ms` }">
          <div class="rail" aria-hidden="true">
            <span class="node"></span>
          </div>
          <div class="body">
            <span class="no num">STEP {{ s.no }}</span>
            <h3 class="t">{{ s.title }}</h3>
            <p class="b">{{ s.body }}</p>
          </div>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.steps {
  list-style: none;
  margin: 56px 0 0;
  padding: 0;
}

.step {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 22px;
}

/* 竖轴 + 节点: 把四道关串成一条流水线 */
.rail {
  position: relative;
  display: flex;
  justify-content: center;
}

.rail::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--line);
}

.step:first-child .rail::before {
  top: 12px;
}

.step:last-child .rail::before {
  bottom: auto;
  height: 12px;
}

.node {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  margin-top: 8px;
  background: var(--ink-1);
  border: 1px solid var(--amber);
  transform: rotate(45deg);
  transition:
    background 0.3s ease,
    box-shadow 0.3s ease;
}

.step:hover .node {
  background: var(--amber);
  box-shadow: 0 0 0 5px var(--amber-glow);
}

.body {
  padding: 0 0 40px;
}

.step:last-child .body {
  padding-bottom: 0;
}

.no {
  font-size: 11px;
  letter-spacing: 0.2em;
  color: var(--fg-faint);
}

.t {
  font-size: 20px;
  margin: 8px 0 10px;
}

.b {
  font-size: 14.5px;
  color: var(--fg-dim);
  line-height: 1.85;
  max-width: 66ch;
}

@media (max-width: 768px) {
  .steps {
    margin-top: 40px;
  }
  .step {
    grid-template-columns: 22px 1fr;
    gap: 16px;
  }
  .t {
    font-size: 18px;
  }
  .body {
    padding-bottom: 30px;
  }
}
</style>
