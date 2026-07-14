<script setup lang="ts">
import { CAPS } from '../content'
</script>

<template>
  <section class="sec" id="caps">
    <div class="wrap">
      <p class="sec-tag reveal">02 / 它替你做什么</p>
      <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">盯盘 · 推送 · 回测 · 复盘</h2>
      <p class="sec-lede reveal" :style="{ '--d': '120ms' }">
        四件事，构成一个闭环：系统替你看盘，命中了叫你；规则上线前先被数据审一遍；事后再拿你的真实成交对着模型算一次账。
      </p>

      <div class="caps">
        <article v-for="(c, i) in CAPS" :key="c.no" class="cap reveal" :style="{ '--d': `${160 + i * 80}ms` }">
          <header class="cap-h">
            <span class="cap-no num">{{ c.no }}</span>
            <span class="cap-name">{{ c.name }}</span>
          </header>

          <h3 class="cap-t">{{ c.title }}</h3>
          <p class="cap-b">{{ c.body }}</p>

          <ul class="cap-l">
            <li v-for="it in c.items" :key="it">
              <span class="tick" aria-hidden="true"></span>{{ it }}
            </li>
          </ul>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.caps {
  margin-top: 56px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1px;
  background: var(--line-soft);
  border: 1px solid var(--line-soft);
}

.cap {
  background: var(--ink-1);
  padding: 38px 34px;
  position: relative;
  transition: background 0.35s ease;
}

/* hover 时左缘亮起一道琥珀 —— 终端选中行的感觉 */
.cap::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--amber);
  transform: scaleY(0);
  transform-origin: top;
  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}

.cap:hover {
  background: var(--ink-2);
}

.cap:hover::before {
  transform: scaleY(1);
}

.cap-h {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 18px;
}

.cap-no {
  font-size: 12px;
  color: var(--amber);
  letter-spacing: 0.1em;
}

.cap-name {
  font-family: var(--font-serif);
  font-size: 14px;
  color: var(--fg-faint);
  letter-spacing: 0.3em;
}

.cap-t {
  font-size: 22px;
  line-height: 1.45;
  margin-bottom: 14px;
}

.cap-b {
  font-size: 14.5px;
  color: var(--fg-dim);
  line-height: 1.8;
  margin-bottom: 24px;
}

.cap-l {
  list-style: none;
  margin: 0;
  padding: 20px 0 0;
  border-top: 1px dashed var(--line);
  display: grid;
  gap: 9px;
}

.cap-l li {
  font-size: 13.5px;
  color: var(--fg);
  display: flex;
  align-items: center;
  gap: 10px;
}

.tick {
  width: 5px;
  height: 5px;
  flex: none;
  background: var(--fg-faint);
  transform: rotate(45deg);
  transition: background 0.3s ease;
}

.cap:hover .tick {
  background: var(--amber);
}

@media (max-width: 860px) {
  .caps {
    grid-template-columns: 1fr;
    margin-top: 40px;
  }
  .cap {
    padding: 28px 22px;
  }
  .cap-t {
    font-size: 19px;
  }
}
</style>
