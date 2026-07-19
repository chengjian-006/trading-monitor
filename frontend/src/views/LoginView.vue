<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, NIcon } from 'naive-ui'
import { useGlobalMessage } from '../composables/useGlobalMessage'
import { LogInOutline, PersonOutline, LockClosedOutline } from '@vicons/ionicons5'
import { useAuthStore } from '../stores/auth'
import BrandMark from '../components/common/BrandMark.vue'
import { fetchPublicOverview, type PublicOverview } from '../api/market-report'

const authStore = useAuthStore()
const router = useRouter()
const message = useGlobalMessage()

const username = ref('')
const password = ref('')
const loading = ref(false)

// 金融格言池 — 加载时随机抽一条
const SAYINGS = [
  '不抓顶, 不抄底, 吃中间一段',
  '会买的是徒弟, 会卖的是师傅',
  '止损是交易者的好朋友',
  '趋势是最好的朋友',
  '量在价先, 看不懂量就别动',
  '强势市做加法, 弱势市做减法',
  '不在恐惧中买入, 不在贪婪中卖出',
  '少即是多, 持仓宁缺勿滥',
]
const saying = computed(() => SAYINGS[Math.floor(Math.random() * SAYINGS.length)])

// ── 实时大盘小卡 ──
const overview = ref<PublicOverview | null>(null)
let overviewTimer: number | null = null

async function loadOverview() {
  try {
    overview.value = await fetchPublicOverview()
  } catch {
    /* silent */
  }
}

// ── 粒子背景 (canvas) — 成熟稳重: 低饱和灰蓝 + 慢速 + 低密度 ──
const canvasEl = ref<HTMLCanvasElement | null>(null)
let animationId: number | null = null
let particleResize: (() => void) | null = null   // 组件级引用, 供 onUnmounted 移除粒子背景 resize 监听

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  r: number
  alpha: number
}

function setupParticles() {
  const canvas = canvasEl.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const PARTICLE_COUNT = 32  // 克制的密度
  const particles: Particle[] = []

  function resize() {
    if (!canvas) return
    const rect = canvas.parentElement?.getBoundingClientRect()
    if (!rect) return
    canvas.width = rect.width * window.devicePixelRatio
    canvas.height = rect.height * window.devicePixelRatio
    canvas.style.width = rect.width + 'px'
    canvas.style.height = rect.height + 'px'
    ctx!.scale(window.devicePixelRatio, window.devicePixelRatio)
  }
  resize()
  particleResize = resize
  window.addEventListener('resize', resize)

  // 初始化粒子 — 随机分布, 慢速漂浮
  const w = canvas.width / window.devicePixelRatio
  const h = canvas.height / window.devicePixelRatio
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    particles.push({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.18,  // 极慢
      vy: (Math.random() - 0.5) * 0.18,
      r: 0.8 + Math.random() * 1.4,
      alpha: 0.25 + Math.random() * 0.4,
    })
  }

  function tick() {
    if (!ctx || !canvas) return
    const W = canvas.width / window.devicePixelRatio
    const H = canvas.height / window.devicePixelRatio
    ctx.clearRect(0, 0, W, H)

    // 画粒子之间的连线 (距离 < 140px 时)
    ctx.strokeStyle = '#5a7a9f'
    ctx.lineWidth = 0.4
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x
        const dy = particles[i].y - particles[j].y
        const d = Math.sqrt(dx * dx + dy * dy)
        if (d < 140) {
          ctx.globalAlpha = (1 - d / 140) * 0.18
          ctx.beginPath()
          ctx.moveTo(particles[i].x, particles[i].y)
          ctx.lineTo(particles[j].x, particles[j].y)
          ctx.stroke()
        }
      }
    }

    // 画粒子
    for (const p of particles) {
      p.x += p.vx
      p.y += p.vy
      // 出界从对面回来 (循环空间)
      if (p.x < 0) p.x = W
      if (p.x > W) p.x = 0
      if (p.y < 0) p.y = H
      if (p.y > H) p.y = 0
      ctx.globalAlpha = p.alpha
      ctx.fillStyle = '#5a7a9f'
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.globalAlpha = 1

    animationId = requestAnimationFrame(tick)
  }
  tick()
}

// 锁 body 滚动 — PC 上登录页固定 1 屏, 移动端需可滚故 768px 以下不锁
const ROOT_NO_SCROLL_MQ = '(min-width: 769px)'
function lockBodyScroll() {
  if (window.matchMedia(ROOT_NO_SCROLL_MQ).matches) {
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
  }
}
function unlockBodyScroll() {
  document.documentElement.style.overflow = ''
  document.body.style.overflow = ''
}

onMounted(() => {
  if (sessionStorage.getItem('kicked') === '1') {
    sessionStorage.removeItem('kicked')
    message.warning('账号已在其他设备登录, 当前会话已退出')
  }
  loadOverview()
  overviewTimer = window.setInterval(loadOverview, 30_000)
  setupParticles()
  lockBodyScroll()
  window.addEventListener('resize', lockBodyScroll)
})

onUnmounted(() => {
  if (overviewTimer) clearInterval(overviewTimer)
  if (animationId) cancelAnimationFrame(animationId)
  if (particleResize) window.removeEventListener('resize', particleResize)   // 修粒子背景 resize 监听泄漏
  window.removeEventListener('resize', lockBodyScroll)
  unlockBodyScroll()
})

async function handleLogin() {
  if (!username.value || !password.value) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await authStore.login(username.value, password.value)
    message.success('登录成功')
    router.replace('/')
  } catch {
    message.error('用户名或密码错误')
  } finally {
    loading.value = false
  }
}

function pctText(pct: number): string {
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}
function pctColor(pct: number): string {
  return pct > 0 ? '#D92B26' : pct < 0 ? '#0F8A5F' : '#8B949E'
}
</script>

<template>
  <div class="login-page">
    <!-- 左侧 brand 区 (PC) / 顶部横条 (移动) -->
    <div class="brand-side">
      <!-- 粒子 canvas 背景 -->
      <canvas ref="canvasEl" class="bg-canvas" aria-hidden="true" />

      <!-- 超大水印 "观潮" -->
      <div class="brand-watermark" aria-hidden="true">观潮</div>

      <!-- 装饰 SVG: 趋势线 + 蜡烛 (淡) -->
      <svg class="bg-kline" viewBox="0 0 400 800" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g opacity="0.12">
          <rect x="34" y="195" width="10" height="35" fill="#D92B26" />
          <rect x="64" y="215" width="10" height="35" fill="#0F8A5F" />
          <rect x="94" y="195" width="10" height="25" fill="#D92B26" />
          <rect x="124" y="180" width="10" height="30" fill="#D92B26" />
          <rect x="154" y="170" width="10" height="30" fill="#D92B26" />
          <rect x="184" y="160" width="10" height="30" fill="#D92B26" />
          <rect x="214" y="150" width="10" height="35" fill="#D92B26" />
          <rect x="244" y="140" width="10" height="30" fill="#D92B26" />
          <rect x="274" y="170" width="10" height="30" fill="#0F8A5F" />
          <rect x="304" y="155" width="10" height="30" fill="#D92B26" />
          <rect x="334" y="135" width="10" height="35" fill="#D92B26" />
        </g>
        <polyline
          points="20,260 50,250 80,240 110,220 140,200 170,185 200,170 230,160 260,165 290,180 320,165 350,150 380,140"
          fill="none" stroke="#0969DA" stroke-width="1.4" opacity="0.30" stroke-linecap="round" stroke-linejoin="round"
        />
      </svg>

      <div class="brand-content">
        <div class="brand-logo">
          <BrandMark :size="40" :radius="11" glow />
          <span class="brand-name">观潮</span>
        </div>
        <div class="brand-tagline">
          <div class="tagline-2">交易信号平台</div>
        </div>
        <div class="brand-features">
          <div class="feature">实时信号 · 板块共振 · 卖点撤销</div>
          <div class="feature">回测复盘 · AI 分析 · 闭环复盘</div>
        </div>

        <!-- 实时大盘小卡 -->
        <div v-if="overview && overview.indices.length" class="market-card">
          <div class="market-card-head">
            <span>大盘实时</span>
            <span v-if="overview.snapshot_at" class="market-snapshot-at">
              {{ overview.snapshot_at.slice(11, 16) }}
            </span>
          </div>
          <div class="market-indices">
            <div v-for="idx in overview.indices" :key="idx.name" class="market-idx">
              <span class="idx-name">{{ idx.name }}</span>
              <span class="idx-price">{{ idx.price.toFixed(2) }}</span>
              <span class="idx-pct" :style="{ color: pctColor(idx.pct_change) }">{{ pctText(idx.pct_change) }}</span>
            </div>
          </div>
          <div class="market-stats">
            <span>涨停 <b style="color:#D92B26">{{ overview.limit_up }}</b></span>
            <span class="market-stats-sep">·</span>
            <span>跌停 <b style="color:#0F8A5F">{{ overview.limit_down }}</b></span>
          </div>
        </div>

        <div class="brand-saying">
          <div class="saying-label">今日格言</div>
          <div class="saying-text">"{{ saying }}"</div>
        </div>
      </div>
    </div>

    <!-- 右侧表单区 -->
    <div class="form-side">
      <div class="form-wrap">
        <div class="form-header">
          <div class="form-title">登录</div>
          <div class="form-subtitle">账号管理 · 个性化信号</div>
        </div>

        <div class="form-fields">
          <NInput
            v-model:value="username"
            placeholder="用户名"
            size="large"
            :input-props="{ name: 'username', autocomplete: 'username', type: 'text' }"
            @keyup.enter="handleLogin"
          >
            <template #prefix>
              <NIcon :component="PersonOutline" :size="18" color="#94a3b8" />
            </template>
          </NInput>

          <NInput
            v-model:value="password"
            type="password"
            placeholder="密码"
            size="large"
            show-password-on="click"
            :input-props="{ name: 'password', autocomplete: 'current-password' }"
            @keyup.enter="handleLogin"
          >
            <template #prefix>
              <NIcon :component="LockClosedOutline" :size="18" color="#94a3b8" />
            </template>
          </NInput>

          <NButton
            type="primary"
            size="large"
            block
            :loading="loading"
            class="login-btn"
            @click="handleLogin"
          >
            <template #icon><NIcon :component="LogInOutline" /></template>
            登 录
          </NButton>
        </div>

        <div class="form-footer">
          <span>© 观潮 · 仅供内部学习使用</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  overflow: hidden;
  display: flex;
  background: var(--bg-default);
}

/* ── 左侧 brand 区（基线0 v1.7.646: 深色渐变退役，浅色统一）── */
.brand-side {
  position: relative;
  flex: 0 0 42%;
  min-height: 100vh;
  overflow: hidden;
  background: linear-gradient(180deg, #F6F8FA 0%, #EDF2F8 100%);
  border-right: 1px solid var(--border-default);
  color: var(--fg-default);
}
.bg-canvas {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}
.bg-kline {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 0;
}
/* 超大品牌字水印 — 极淡, 宋体 */
.brand-watermark {
  position: absolute;
  right: -60px;
  bottom: -80px;
  font-size: 280px;
  font-family: "STSong", "SimSun", "Songti SC", serif;
  font-weight: 700;
  letter-spacing: 30px;
  color: #1F2328;
  opacity: 0.035;
  user-select: none;
  pointer-events: none;
  line-height: 0.95;
  z-index: 0;
}
.brand-content {
  position: relative;
  z-index: 2;
  padding: 56px 48px;
  height: 100%;
  display: flex;
  flex-direction: column;
}
.brand-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--accent-fg);
}
.brand-name {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 6px;
  color: var(--fg-default);
}
.brand-tagline {
  margin-top: 56px;
}
.tagline-2 {
  font-size: 30px;
  font-weight: 600;
  color: var(--fg-default);
  letter-spacing: 2px;
}
.brand-features {
  margin-top: 22px;
  font-size: 13px;
  line-height: 2.1;
  color: var(--fg-muted);
  letter-spacing: 0.5px;
}

/* 实时大盘小卡 */
.market-card {
  margin-top: 32px;
  padding: 14px 16px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
}
.market-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--fg-subtle);
  letter-spacing: 2px;
  margin-bottom: 10px;
}
.market-snapshot-at {
  font-family: monospace;
  font-variant-numeric: tabular-nums;
  color: var(--accent-fg);
  letter-spacing: 1px;
}
.market-indices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 16px;
}
.market-idx {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}
.idx-name {
  color: var(--fg-muted);
  flex: 0 0 auto;
  width: 56px;
}
.idx-price {
  font-family: monospace;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--fg-default);
  white-space: nowrap;
}
.idx-pct {
  font-family: monospace;
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  margin-left: auto;
  white-space: nowrap;
}
.market-stats {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-muted);
  font-size: 12px;
  color: var(--fg-muted);
}
.market-stats b {
  font-family: monospace;
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  margin-left: 2px;
  margin-right: 4px;
}
.market-stats-sep {
  margin: 0 8px;
  color: var(--fg-subtle);
}

.brand-saying {
  margin-top: auto;
  padding-top: 24px;
}
.saying-label {
  font-size: 11px;
  color: var(--accent-fg);
  letter-spacing: 3px;
  margin-bottom: 8px;
}
.saying-text {
  font-size: 15px;
  font-style: italic;
  color: var(--fg-muted);
  line-height: 1.6;
}

/* ── 右侧表单区 ── */
.form-side {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
}
.form-wrap {
  width: 100%;
  max-width: 380px;
}
.form-header {
  margin-bottom: 36px;
}
.form-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--fg-default);
  margin-bottom: 6px;
}
.form-subtitle {
  font-size: 13px;
  color: var(--fg-muted);
  letter-spacing: 0.5px;
}
.form-fields {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.login-btn {
  margin-top: 8px;
  height: 44px;
  touch-action: manipulation;
  font-size: 15px;
  letter-spacing: 6px;
}
.form-footer {
  margin-top: 32px;
  text-align: center;
  font-size: 11px;
  color: var(--fg-subtle);
  letter-spacing: 0.5px;
}

/* ── 响应式: 移动端折成顶部 brand + 下方表单 ── */
@media (max-width: 768px) {
  .login-page {
    /* 移动端纵向堆叠后内容会超出 100vh, 必须允许滚动 */
    height: auto;
    min-height: 100vh;
    overflow: auto;
    flex-direction: column;
  }
  .brand-side {
    flex: 0 0 auto;
    min-height: auto;
    padding-bottom: 8px;
  }
  .brand-content {
    padding: 28px 24px 20px;
    height: auto;
  }
  .brand-logo {
    justify-content: center;
  }
  .brand-tagline {
    margin-top: 14px;
    text-align: center;
  }
  .tagline-2 {
    font-size: 22px;
    margin-top: 4px;
  }
  .brand-features {
    margin-top: 12px;
    text-align: center;
    line-height: 1.8;
  }
  .market-card {
    margin-top: 14px;
  }
  /* 手机端: 2列太窄致指数数字逐字换行, 改单列每行全宽; 名称收窄给数字让位 */
  .market-indices {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .idx-name {
    width: 68px;
  }
  .brand-watermark {
    font-size: 180px;
    right: -30px;
    bottom: -40px;
  }
  .brand-saying {
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px dashed var(--border-default);
    text-align: center;
  }
  .saying-text {
    font-size: 13px;
  }
  .form-side {
    padding: 32px 24px;
  }
  .form-header {
    margin-bottom: 24px;
    text-align: center;
  }
  .form-title {
    font-size: 22px;
  }
}

/* ── 平板段 ── */
@media (min-width: 769px) and (max-width: 1024px) {
  .brand-side {
    flex: 0 0 36%;
  }
  .brand-content {
    padding: 40px 32px;
  }
  .tagline-2 {
    font-size: 24px;
  }
}
</style>
