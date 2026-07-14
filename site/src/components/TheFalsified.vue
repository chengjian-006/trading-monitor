<script setup lang="ts">
import { FALSIFIED } from '../content'
</script>

<template>
  <section class="sec falsified" id="falsified">
    <div class="wrap">
      <p class="sec-tag reveal">04 / 证伪记录</p>
      <h2 class="sec-title reveal" :style="{ '--d': '60ms' }">{{ FALSIFIED.title }}</h2>
      <p class="sec-lede reveal" :style="{ '--d': '120ms' }">{{ FALSIFIED.lede }}</p>

      <ul class="rows">
        <li
          v-for="(f, i) in FALSIFIED.items"
          :key="i"
          class="row reveal"
          :style="{ '--d': `${140 + i * 55}ms` }"
        >
          <div class="claim">
            <span class="idx num">{{ String(i + 1).padStart(2, '0') }}</span>
            <p class="claim-t">「{{ f.claim }}」</p>
          </div>
          <p class="verdict">
            <span class="label num">回测结论</span>
            {{ f.verdict }}
          </p>
        </li>
      </ul>

      <aside class="caveat reveal">
        <span class="warn num">未验证的风险</span>
        <p>{{ FALSIFIED.caveat }}</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
/* 这一屏底色压深一档: 视觉上是全站的"沉下来的地方" */
.falsified {
  background: linear-gradient(180deg, transparent, rgba(8, 9, 12, 0.72) 18%, rgba(8, 9, 12, 0.72));
}

.rows {
  list-style: none;
  margin: 56px 0 0;
  padding: 0;
}

.row {
  padding: 26px 0;
  border-top: 1px solid var(--line-soft);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 40px;
  align-items: start;
}

.row:last-child {
  border-bottom: 1px solid var(--line-soft);
}

.claim {
  display: flex;
  gap: 16px;
}

.idx {
  font-size: 12px;
  color: var(--fg-faint);
  padding-top: 7px;
  flex: none;
}

/* 被划掉的直觉: 衬线体 + 删除线 + 压暗, 一眼就知道"这条死了" */
.claim-t {
  font-family: var(--font-serif);
  font-size: clamp(16px, 2.1vw, 19px);
  line-height: 1.7;
  color: var(--fg-faint);
  text-decoration: line-through;
  text-decoration-color: var(--up);
  text-decoration-thickness: 1.5px;
  transition: color 0.3s ease;
}

.row:hover .claim-t {
  color: var(--fg-dim);
}

.verdict {
  font-size: 14.5px;
  line-height: 1.85;
  color: var(--fg);
  padding-left: 20px;
  border-left: 2px solid var(--amber);
}

.label {
  display: block;
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--amber);
  margin-bottom: 6px;
}

/* ---------- 未测风险 ---------- */
.caveat {
  margin-top: 48px;
  padding: 26px 28px;
  border: 1px solid rgba(229, 72, 77, 0.32);
  background: rgba(229, 72, 77, 0.05);
}

.warn {
  display: block;
  font-size: 11px;
  letter-spacing: 0.18em;
  color: var(--up);
  margin-bottom: 10px;
}

.caveat p {
  font-size: 14.5px;
  line-height: 1.85;
  color: var(--fg-dim);
}

@media (max-width: 860px) {
  .rows {
    margin-top: 40px;
  }
  .row {
    grid-template-columns: 1fr;
    gap: 16px;
    padding: 22px 0;
  }
  .verdict {
    padding-left: 16px;
    font-size: 14px;
  }
  .caveat {
    padding: 20px;
    margin-top: 36px;
  }
}
</style>
