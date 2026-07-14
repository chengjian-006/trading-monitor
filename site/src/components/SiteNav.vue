<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { HERO } from '../content'

const solid = ref(false)
const onScroll = () => (solid.value = window.scrollY > 40)

onMounted(() => {
  onScroll()
  window.addEventListener('scroll', onScroll, { passive: true })
})
onUnmounted(() => window.removeEventListener('scroll', onScroll))
</script>

<template>
  <header class="nav" :class="{ solid }">
    <div class="wrap nav-in">
      <a href="#top" class="brand">
        <span class="mark" aria-hidden="true"></span>
        <span class="brand-name">{{ HERO.brand }}</span>
      </a>

      <nav class="links" aria-label="主导航">
        <a href="#caps">能力</a>
        <a href="#method">方法</a>
        <a href="#falsified">证伪记录</a>
        <a href="#faq">常见问题</a>
      </nav>

      <a href="#apply" class="nav-cta">申请内测</a>
    </div>
  </header>
</template>

<style scoped>
.nav {
  position: fixed;
  inset: 0 0 auto 0;
  z-index: 50;
  transition:
    background 0.3s ease,
    border-color 0.3s ease,
    backdrop-filter 0.3s ease;
  border-bottom: 1px solid transparent;
}

.nav.solid {
  background: rgba(10, 12, 16, 0.82);
  backdrop-filter: blur(14px) saturate(140%);
  border-bottom-color: var(--line-soft);
}

.nav-in {
  height: 62px;
  display: flex;
  align-items: center;
  gap: 28px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-right: auto;
}

/* 品牌标记: 一根定格的分时线, 比放个 logo 图更贴调性 */
.mark {
  width: 18px;
  height: 18px;
  flex: none;
  background: var(--amber);
  clip-path: polygon(0 72%, 22% 48%, 42% 62%, 64% 18%, 82% 38%, 100% 4%, 100% 100%, 0 100%);
  opacity: 0.92;
}

.brand-name {
  font-family: var(--font-serif);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg-strong);
  letter-spacing: 0.06em;
}

.links {
  display: flex;
  gap: 26px;
  font-size: 14px;
  color: var(--fg-dim);
}

.links a {
  position: relative;
  padding: 4px 0;
  transition: color 0.2s ease;
}

.links a::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: 0;
  width: 0;
  height: 1px;
  background: var(--amber);
  transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

.links a:hover {
  color: var(--fg-strong);
}

.links a:hover::after {
  width: 100%;
}

.nav-cta {
  font-family: var(--font-mono);
  font-size: 13px;
  letter-spacing: 0.04em;
  padding: 8px 16px;
  border: 1px solid var(--line);
  color: var(--fg);
  transition:
    border-color 0.2s ease,
    color 0.2s ease,
    background 0.2s ease;
}

.nav-cta:hover {
  border-color: var(--amber);
  color: var(--amber);
  background: var(--amber-glow);
}

/* 手机: 导航只留品牌 + 申请, 中间链接收起(页面本来就是一条长滚动, 不缺入口) */
@media (max-width: 720px) {
  .links {
    display: none;
  }
  .nav-in {
    height: 54px;
  }
}
</style>
