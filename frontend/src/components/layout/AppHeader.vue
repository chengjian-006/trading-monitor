<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'
import { LogOutOutline, PersonOutline } from '@vicons/ionicons5'
import { useAuthStore } from '../../stores/auth'
import ApiHealthIndicator from './ApiHealthIndicator.vue'
import MarketRiskLight from './MarketRiskLight.vue'
import BrandMark from '../common/BrandMark.vue'

const router = useRouter()
const authStore = useAuthStore()

defineProps<{ connected: boolean }>()

const envInfo = computed(() => {
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') {
    return { label: 'DEV', color: 'var(--success-fg)' }
  }
  return { label: 'PROD', color: 'var(--danger-fg)' }
})

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <header class="app-navbar">
    <div class="navbar-brand">
      <BrandMark :size="30" :radius="8" glow />
      <div class="brand-text">
        <span class="brand-title">观潮</span>
        <span class="brand-subtitle">智能监控系统</span>
      </div>
      <span class="env-tag" :style="{ color: envInfo.color, borderColor: envInfo.color }">{{ envInfo.label }}</span>
    </div>

    <!-- 状态母线: 大盘风险 / 接口健康 / 后端连接, 统一圆点+mono标签+发丝竖线 -->
    <div class="navbar-status">
      <MarketRiskLight />
      <ApiHealthIndicator />
      <span :class="['stat-cell', 'ws-status', { off: !connected }]"
            :title="connected ? '后端服务在线 (WebSocket 连接正常)' : '后端服务离线 (WebSocket 已断开)'"
            :aria-label="connected ? '后端服务在线' : '后端服务离线'">
        <span class="ws-dot" />
        <span class="ws-label">{{ connected ? '后端在线' : '后端离线' }}</span>
      </span>
    </div>

    <!-- 用户操作组: 与状态母线用更强分隔隔开 -->
    <div class="navbar-user-group">
      <span class="navbar-user"><NIcon :component="PersonOutline" :size="13" />{{ authStore.user?.username }}</span>
      <NButton size="small" quaternary @click="handleLogout">
        <template #icon><NIcon><LogOutOutline /></NIcon></template>
        退出
      </NButton>
    </div>
  </header>
</template>

<style scoped>
/* 机构级状态母线 (v1.7.650): 冷调 + mono 状态 + 发丝分隔 */
.app-navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 20px;
  background: var(--bg-head);
  border-bottom: 1px solid var(--border-hard);
  position: sticky;
  top: 0;
  z-index: 200;
}

.navbar-brand {
  display: flex;
  align-items: center;
  gap: 11px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-title {
  color: var(--fg-default);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand-subtitle {
  color: var(--fg-subtle);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 500;
  letter-spacing: .18em;
  text-transform: uppercase;
}

/* 状态母线: 靠右, 发丝竖线分隔的等高状态单元 */
.navbar-status {
  display: flex;
  align-items: stretch;
  height: 100%;
  margin-left: auto;
}
/* 每个状态单元统一: 圆点+mono标签, 左侧发丝竖线, 等内边距 */
.navbar-status > * {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  border-left: 1px solid var(--border-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  letter-spacing: .04em;
  white-space: nowrap;
}

.stat-cell.ws-status .ws-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--success-fg);
  box-shadow: 0 0 6px var(--success-fg);
}
.ws-status.off .ws-dot {
  background: var(--danger-fg);
  box-shadow: 0 0 6px var(--danger-fg);
}
.ws-label { color: var(--fg-muted); }
.ws-status.off .ws-label { color: var(--danger-fg); }

/* 用户操作组: 与状态母线用更强分隔(硬边框)隔开 */
.navbar-user-group {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 100%;
  padding-left: 14px;
  margin-left: 4px;
  border-left: 1px solid var(--border-hard);
}
.navbar-user {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: .04em;
  color: var(--fg-muted);
}
.navbar-user :deep(.n-icon) { color: var(--fg-subtle); }

.env-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: 3px;
  font-weight: 700;
  margin-left: 12px;
  letter-spacing: .12em;
}

@media (max-width: 768px) {
  .app-navbar { padding: 0 12px; }
  .brand-subtitle { display: none; }
  .navbar-status > * { padding: 0 9px; font-size: 11px; }
  .ws-status .ws-label { display: none; }   /* 手机窄: 后端连接只留圆点 */
  .navbar-user { display: none; }            /* 手机窄: 用户名藏, 只留退出 */
  .navbar-user-group { padding-left: 8px; }
}

</style>
