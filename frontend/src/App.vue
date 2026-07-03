<script setup lang="ts">
import { useResponsive } from './composables/useResponsive'
import { useWebSocket } from './composables/useWebSocket'
import { useSignalStore } from './stores/signal'
import { useAuthStore } from './stores/auth'
import AppHeader from './components/layout/AppHeader.vue'
import AppSidebar from './components/layout/AppSidebar.vue'
import AppTabBar from './components/layout/AppTabBar.vue'
import MarketRiskLight from './components/layout/MarketRiskLight.vue'
import StockDetailModal from './components/chart/StockDetailModal.vue'
import { computed, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NConfigProvider, NModal, NCard, type GlobalThemeOverrides } from 'naive-ui'
import './composables/useGlobalMessage'
import { useGlobalDialog } from './composables/useGlobalMessage'
import { fetchUploadStatus } from './api/trade-analysis'

const { isMobile } = useResponsive()
const signalStore = useSignalStore()
const authStore = useAuthStore()
const route = useRoute()

const isLoginPage = computed(() => route.name === 'login')
const router = useRouter()

// 登录后检查: 晚9点后若今日未上传交割单, 弹一次提醒(每日一次)
onMounted(async () => {
  if (!authStore.isLoggedIn) return
  if (!authStore.user) {
    const ok = await authStore.fetchMe()
    if (!ok) return
  }
  const todayKey = `trade_remind_${new Date().toISOString().slice(0, 10)}`
  if (localStorage.getItem(todayKey)) return
  try {
    const st = await fetchUploadStatus()
    if (st.should_remind) {
      localStorage.setItem(todayKey, '1')
      useGlobalDialog().warning({
        title: '交割单提醒',
        content: '今天还没上传交割单。要不要现在去「交易分析」上传当日交割单做复盘？',
        positiveText: '去上传',
        negativeText: '稍后',
        onPositiveClick: () => router.push('/trade-analysis'),
      })
    }
  } catch { /* silent */ }
})

const showKickModal = ref(false)
const kickCountdown = ref(5)

const { connected } = useWebSocket((data) => {
  if (data.type === 'force_logout') {
    ;(window as any).__forceLogoutInProgress = true
    authStore.logout()
    showKickModal.value = true
    kickCountdown.value = 5
    const timer = setInterval(() => {
      kickCountdown.value--
      if (kickCountdown.value <= 0) {
        clearInterval(timer)
        window.location.href = '/login'
      }
    }, 1000)
    return
  }
  if (data.type === 'signal') {
    signalStore.addSignal(data)
  }
})

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#0969DA',
    primaryColorHover: '#0860C4',
    primaryColorPressed: '#0757B0',
    successColor: '#1A7F37',
    errorColor: '#CF222E',
    warningColor: '#BF8700',
    borderColor: '#D1D9E0',
    textColorBase: '#1F2328',
    bodyColor: '#F6F8FA',
    cardColor: '#FFFFFF',
  },
  DataTable: {
    thColor: 'rgba(9, 105, 218, 0.12)',
    thTextColor: '#1F2328',
    thFontWeight: '600',
    thBorderColor: 'rgba(9, 105, 218, 0.25)',
    thPaddingSmall: '10px 12px',
  },
}

</script>

<template>
  <NConfigProvider :theme-overrides="themeOverrides">
    <!-- PC Layout -->
    <div v-if="!isMobile" class="app-layout-pc">
      <AppHeader v-if="!isLoginPage" :connected="connected" />
      <div :class="isLoginPage ? '' : 'pc-body'">
        <AppSidebar v-if="!isLoginPage" />
        <main :class="isLoginPage ? '' : 'pc-main'">
          <router-view v-slot="{ Component }">
            <Transition name="page" mode="out-in">
              <component :is="Component" />
            </Transition>
          </router-view>
        </main>
      </div>
    </div>

    <!-- Mobile Layout -->
    <div v-else class="app-layout">
      <template v-if="!isLoginPage">
        <div class="mobile-header">
          <span class="mobile-brand">观潮</span>
          <div class="mobile-header-right">
            <MarketRiskLight />
            <span class="mobile-user">{{ authStore.user?.username }}</span>
            <span :class="['status-dot', { off: !connected }]" />
          </div>
        </div>
      </template>
      <main :class="['main-content', { mobile: !isLoginPage }]">
        <router-view v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </main>
      <AppTabBar v-if="!isLoginPage" />
    </div>
    <NModal v-model:show="showKickModal" :mask-closable="false" :close-on-esc="false">
      <NCard style="width: 380px; max-width: 90vw; text-align: center" :bordered="false">
        <div style="font-size: 48px; margin-bottom: 16px">⚠️</div>
        <div style="font-size: 16px; font-weight: 600; color: #1f2328; margin-bottom: 12px">你已在其他电脑登录，即将退出</div>
        <div style="font-size: 14px; color: #656d76">{{ kickCountdown }} 秒后返回登录页</div>
      </NCard>
    </NModal>
    <!-- 通用个股详情弹窗(全局单实例): 任意组件 useUiStore().openStock(code,name) 即弹 -->
    <StockDetailModal />
  </NConfigProvider>
</template>

<style scoped>
/* PC Layout */
.app-layout-pc {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg);
}

.pc-body {
  display: flex;
  flex: 1;
}

.pc-main {
  flex: 1;
  padding: 24px;
  min-width: 0;
  overflow-y: auto;
  background: var(--bg);
}

/* Mobile Layout */
.app-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.main-content {
  flex: 1;
  padding: 20px;
  width: 100%;
}
.main-content.mobile {
  padding: 12px;
  padding-bottom: 72px;
}
.mobile-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
}
.mobile-brand {
  font-size: 16px;
  font-weight: 700;
  color: var(--primary);
}
.mobile-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mobile-user {
  font-size: 13px;
  color: var(--text2);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--green);
}
.status-dot.off {
  background: var(--red);
}
</style>

<style>
/* Page route transition */
.page-enter-active {
  transition: opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1),
              transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}
.page-leave-active {
  transition: opacity 0.2s ease-out;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(20px);
}
.page-leave-to {
  opacity: 0;
}

/* Content fade-in (skeleton → content) */
.content-fade-enter-active {
  transition: opacity 0.4s cubic-bezier(0.4, 0, 0.2, 1),
              transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}
.content-fade-enter-from {
  opacity: 0;
  transform: translateY(16px);
}
</style>
