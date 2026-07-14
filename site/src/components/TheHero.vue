<script setup lang="ts">
import { HERO } from '../content'
</script>

<template>
  <section id="top" class="hero">
    <div class="wrap hero-in">
      <!-- 左: 主张 -->
      <div class="copy">
        <p class="kicker num">{{ HERO.kicker }}</p>

        <h1 class="title">
          <span v-for="(line, i) in HERO.title.split('\n')" :key="i" class="tline" :style="{ '--i': i }">
            {{ line }}
          </span>
        </h1>

        <p class="lede">{{ HERO.lede }}</p>

        <div class="cta">
          <a href="#apply" class="btn-primary">{{ HERO.ctaPrimary }}</a>
          <a href="#falsified" class="btn-ghost">
            {{ HERO.ctaSecondary }}
            <span class="arrow" aria-hidden="true">↓</span>
          </a>
        </div>
      </div>

      <!-- 右: 拟真推送卡片。示例股为虚构代号, 不指向任何真实标的 -->
      <aside class="term" aria-label="推送示例">
        <div class="term-bar">
          <span class="dot" aria-hidden="true"></span>
          <span class="term-title">{{ HERO.terminal.title }}</span>
          <span class="term-time num">{{ HERO.terminal.time }}</span>
        </div>

        <dl class="term-body">
          <div v-for="(l, i) in HERO.terminal.lines" :key="l.k" class="row" :style="{ '--i': i + 1 }">
            <dt>{{ l.k }}</dt>
            <dd :class="l.tone">{{ l.v }}</dd>
          </div>
        </dl>

        <p class="term-foot">{{ HERO.terminal.foot }}</p>
      </aside>
    </div>

    <div class="scroll-hint" aria-hidden="true">
      <span class="num">SCROLL</span>
      <span class="bar"></span>
    </div>
  </section>
</template>

<style scoped>
.hero {
  min-height: 100svh;
  display: flex;
  align-items: center;
  padding: 116px 0 88px;
  position: relative;
  overflow: hidden;
}

.hero-in {
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 64px;
  align-items: center;
}

/* ---------- 左 ---------- */
.kicker {
  font-size: 12px;
  letter-spacing: 0.24em;
  color: var(--amber);
  margin-bottom: 26px;
  opacity: 0;
  animation: rise 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.05s forwards;
}

.title {
  font-size: clamp(30px, 5.4vw, 58px);
  line-height: 1.28;
  margin-bottom: 28px;
}

/* 逐行抬升: 首屏唯一的编排动画, 一次到位胜过满页小动效 */
.tline {
  display: block;
  opacity: 0;
  animation: rise 0.9s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  animation-delay: calc(0.18s + var(--i) * 0.13s);
}

.lede {
  color: var(--fg-dim);
  font-size: clamp(15px, 2vw, 17px);
  max-width: 54ch;
  margin-bottom: 38px;
  opacity: 0;
  animation: rise 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.52s forwards;
}

.cta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  opacity: 0;
  animation: rise 0.9s cubic-bezier(0.16, 1, 0.3, 1) 0.66s forwards;
}

.btn-primary {
  font-family: var(--font-mono);
  font-size: 14px;
  letter-spacing: 0.04em;
  padding: 13px 28px;
  background: var(--amber);
  color: var(--ink-0);
  font-weight: 700;
  transition:
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1),
    box-shadow 0.25s ease;
}

.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 32px -10px rgba(240, 165, 0, 0.6);
}

.btn-ghost {
  font-family: var(--font-mono);
  font-size: 14px;
  padding: 13px 22px;
  border: 1px solid var(--line);
  color: var(--fg-dim);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;
}

.btn-ghost:hover {
  color: var(--fg-strong);
  border-color: var(--fg-faint);
}

.btn-ghost:hover .arrow {
  transform: translateY(3px);
}

.arrow {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

/* ---------- 右: 终端卡片 ---------- */
.term {
  background: linear-gradient(160deg, var(--ink-3), var(--ink-2));
  border: 1px solid var(--line);
  box-shadow: 0 32px 70px -30px rgba(0, 0, 0, 0.85);
  opacity: 0;
  animation: rise 1s cubic-bezier(0.16, 1, 0.3, 1) 0.42s forwards;
}

.term-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(0, 0, 0, 0.22);
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--up);
  flex: none;
  animation: pulse 2.4s ease-in-out infinite;
}

.term-title {
  font-size: 13px;
  color: var(--fg-dim);
}

.term-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--fg-faint);
}

.term-body {
  margin: 0;
  padding: 8px 0;
}

.row {
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: 14px;
  padding: 11px 16px;
  opacity: 0;
  animation: rise 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  animation-delay: calc(0.7s + var(--i) * 0.11s);
}

.row + .row {
  border-top: 1px dashed var(--line-soft);
}

dt {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--fg-faint);
  letter-spacing: 0.08em;
  padding-top: 2px;
}

dd {
  margin: 0;
  font-size: 14px;
  color: var(--fg);
  line-height: 1.6;
}

dd.up {
  color: var(--up);
  font-weight: 600;
}

.term-foot {
  padding: 12px 16px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--fg-faint);
  background: rgba(0, 0, 0, 0.18);
}

/* ---------- 滚动提示 ---------- */
.scroll-hint {
  position: absolute;
  left: 50%;
  bottom: 26px;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 9px;
  opacity: 0;
  animation: rise 1s ease 1.3s forwards;
}

.scroll-hint span:first-child {
  font-size: 10px;
  letter-spacing: 0.3em;
  color: var(--fg-faint);
}

.bar {
  width: 1px;
  height: 34px;
  background: linear-gradient(var(--amber), transparent);
  animation: drop 2s ease-in-out infinite;
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    box-shadow: 0 0 0 0 rgba(229, 72, 77, 0.5);
  }
  50% {
    opacity: 0.55;
    box-shadow: 0 0 0 5px rgba(229, 72, 77, 0);
  }
}

@keyframes drop {
  0% {
    transform: scaleY(0);
    transform-origin: top;
  }
  50% {
    transform: scaleY(1);
    transform-origin: top;
  }
  51% {
    transform: scaleY(1);
    transform-origin: bottom;
  }
  100% {
    transform: scaleY(0);
    transform-origin: bottom;
  }
}

/* ---------- 手机 ---------- */
@media (max-width: 900px) {
  .hero-in {
    grid-template-columns: 1fr;
    gap: 44px;
  }
}

@media (max-width: 768px) {
  .hero {
    min-height: auto;
    padding: 96px 0 64px;
  }
  .scroll-hint {
    display: none;
  }
  .cta {
    gap: 10px;
  }
  .btn-primary,
  .btn-ghost {
    flex: 1 1 auto;
    text-align: center;
    justify-content: center;
  }
}
</style>
