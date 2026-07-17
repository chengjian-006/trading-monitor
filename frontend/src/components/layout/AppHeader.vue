<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'
import { LogOutOutline } from '@vicons/ionicons5'
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
      <BrandMark :size="32" :radius="8" glow />
      <div class="brand-text">
        <span class="brand-title">观潮</span>
        <span class="brand-subtitle">智能监控系统</span>
      </div>
      <span class="env-tag" :style="{ color: envInfo.color }">{{ envInfo.label }}</span>
    </div>
    <div class="navbar-right">
      <MarketRiskLight />
      <ApiHealthIndicator />
      <span :class="['ws-status', { off: !connected }]" :title="connected ? '后端服务在线 (WebSocket 连接正常)' : '后端服务离线 (WebSocket 已断开)'" :aria-label="connected ? '后端服务在线 (WebSocket 连接正常)' : '后端服务离线 (WebSocket 已断开)'">
        <span class="ws-dot" />
        <span class="ws-label">{{ connected ? '后端在线' : '后端离线' }}</span>
      </span>
      <span class="navbar-user">{{ authStore.user?.username }}</span>
      <NButton
        size="small"
        quaternary
        @click="handleLogout"
      >
        <template #icon><NIcon><LogOutOutline /></NIcon></template>
        退出
      </NButton>
    </div>
  </header>
</template>

<style scoped>
/* 基线0 (v1.7.646): 顶栏统一浅色，深色渐变退役 */
.app-navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--navbar-height);
  padding: 0 28px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-default);
  position: sticky;
  top: 0;
  z-index: 200;
}

.navbar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand-title {
  color: var(--fg-default);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
}

.brand-subtitle {
  color: var(--fg-subtle);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 2px;
}

.navbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.ws-status {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ws-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success-fg);
}
.ws-status.off .ws-dot {
  background: var(--danger-fg);
}

.ws-label {
  font-size: 12px;
  color: var(--fg-muted);
}
.ws-status.off .ws-label {
  color: var(--danger-fg);
}

.navbar-user {
  font-size: 13px;
  color: var(--fg-muted);
}

.env-tag {
  font-size: 11px;
  padding: 1px 6px;
  border: 1px solid currentColor;
  border-radius: 4px;
  font-weight: 600;
  margin-left: 12px;
  letter-spacing: 1px;
}

</style>
