<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NIcon } from 'naive-ui'
import { LogOutOutline } from '@vicons/ionicons5'
import { useAuthStore } from '../../stores/auth'
import ApiHealthIndicator from './ApiHealthIndicator.vue'
import MarketRiskLight from './MarketRiskLight.vue'

const router = useRouter()
const authStore = useAuthStore()

defineProps<{ connected: boolean }>()

const envInfo = computed(() => {
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') {
    return { label: 'DEV', color: '#52c41a' }
  }
  return { label: 'PROD', color: '#ff4d4f' }
})

function handleLogout() {
  authStore.logout()
  router.push('/login')
}
</script>

<template>
  <header class="app-navbar">
    <div class="navbar-brand">
      <div class="brand-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M3 3v18h18" /><path d="M7 16l4-8 4 4 4-6" />
        </svg>
      </div>
      <div class="brand-text">
        <span class="brand-title">观潮</span>
        <span class="brand-subtitle">智能监控系统</span>
      </div>
      <span class="env-tag" :style="{ background: envInfo.color }">{{ envInfo.label }}</span>
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
        ghost
        style="color: #a0a0a0; border-color: #444;"
        @click="handleLogout"
      >
        <template #icon><NIcon><LogOutOutline /></NIcon></template>
        退出
      </NButton>
    </div>
  </header>
</template>

<style scoped>
.app-navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--navbar-height);
  padding: 0 28px;
  background: var(--navbar-bg);
  position: sticky;
  top: 0;
  z-index: 200;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.navbar-brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--primary), #0050a0);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(9, 105, 218, 0.35);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}

.brand-title {
  color: #fff;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand-subtitle {
  color: rgba(46, 158, 255, 0.8);
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
  background: var(--green);
}
.ws-status.off .ws-dot {
  background: var(--red);
}

.ws-label {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.6);
}
.ws-status.off .ws-label {
  color: rgba(255, 77, 79, 0.8);
}

.navbar-user {
  font-size: 13px;
  color: #ccc;
}

.env-tag {
  font-size: 11px;
  color: #fff;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
  margin-left: 12px;
  letter-spacing: 1px;
}

</style>
