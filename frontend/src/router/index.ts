import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
    { path: '/', name: 'signals', component: () => import('../views/SignalView.vue') },
    { path: '/pool', name: 'pool', component: () => import('../views/PoolView.vue') },
    { path: '/intraday', name: 'intraday', component: () => import('../views/IntraDayView.vue') },
    { path: '/history', name: 'history', component: () => import('../views/HistoryView.vue') },
    { path: '/popularity', name: 'popularity', component: () => import('../views/PopularityView.vue') },
    { path: '/signal-config', name: 'signal-config', component: () => import('../views/SignalConfigView.vue') },
    { path: '/alert-overview', name: 'alert-overview', component: () => import('../views/AlertOverviewView.vue') },
    { path: '/review', name: 'review', component: () => import('../views/ReviewView.vue') },
    { path: '/models', name: 'models', component: () => import('../views/ModelsView.vue') },
    { path: '/model-backtest', name: 'model-backtest', component: () => import('../views/ModelBacktestView.vue') },
    { path: '/trade-analysis', name: 'trade-analysis', component: () => import('../views/TradeAnalysisView.vue') },
    { path: '/paper-trading', name: 'paper-trading', component: () => import('../views/PaperTradingView.vue') },
    { path: '/logs', name: 'logs', component: () => import('../views/LogView.vue') },
    { path: '/changelog', name: 'changelog', component: () => import('../views/ChangelogView.vue') },
    { path: '/config', name: 'config', component: () => import('../views/ConfigView.vue'), meta: { admin: true } },
    { path: '/scheduled-tasks', name: 'scheduled-tasks', component: () => import('../views/ScheduledTaskView.vue'), meta: { admin: true } },
    { path: '/lark-templates', name: 'lark-templates', component: () => import('../views/LarkTemplateView.vue'), meta: { admin: true } },
    { path: '/users', name: 'users', component: () => import('../views/UserManageView.vue'), meta: { admin: true } },
  ],
})

router.beforeEach(async (to) => {
  ;(window as any).$loadingBar?.start()

  if (to.meta.public) return true

  const authStore = useAuthStore()
  if (!authStore.isLoggedIn) return '/login'

  if (!authStore.user) {
    const ok = await authStore.fetchMe()
    if (!ok) return '/login'
  }

  if (to.meta.admin && authStore.user?.role !== 'admin') {
    return '/'
  }

  return true
})

router.afterEach(() => {
  ;(window as any).$loadingBar?.finish()
})

export default router
