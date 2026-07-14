<script setup lang="ts">
import { FOOTER, HERO } from '../content'
</script>

<template>
  <footer class="foot">
    <div class="wrap">
      <!-- 免责声明放页脚最显眼处, 不折叠、不缩小 -->
      <div class="disc">
        <span class="disc-tag num">风险提示</span>
        <p>{{ FOOTER.disclaimer }}</p>
      </div>

      <div class="bar">
        <span class="brand">
          <span class="mark" aria-hidden="true"></span>
          {{ HERO.brand }}
        </span>

        <span class="icp num">
          <template v-if="FOOTER.icp">
            <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">{{ FOOTER.icp }}</a>
          </template>
          <template v-else>{{ FOOTER.icpHint }}</template>
        </span>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.foot {
  border-top: 1px solid var(--line-soft);
  background: var(--ink-0);
  padding: 52px 0 34px;
}

.disc {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 20px;
  padding-bottom: 30px;
  border-bottom: 1px solid var(--line-soft);
}

.disc-tag {
  font-size: 11px;
  letter-spacing: 0.16em;
  color: var(--up);
  padding-top: 3px;
}

.disc p {
  font-size: 13px;
  line-height: 1.9;
  color: var(--fg-faint);
  max-width: 92ch;
}

.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 26px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  font-family: var(--font-serif);
  font-size: 15px;
  color: var(--fg-dim);
  letter-spacing: 0.06em;
}

.mark {
  width: 14px;
  height: 14px;
  background: var(--fg-faint);
  clip-path: polygon(0 72%, 22% 48%, 42% 62%, 64% 18%, 82% 38%, 100% 4%, 100% 100%, 0 100%);
}

.icp {
  font-size: 12px;
  color: var(--fg-faint);
}

.icp a:hover {
  color: var(--fg-dim);
}

@media (max-width: 640px) {
  .disc {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
}
</style>
