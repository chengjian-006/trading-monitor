<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

// 底栏常用 4 个; 其余进「更多」抽屉 (v1.7.662 对齐新菜单结构)
const primaryTabs = [
  { key: 'signals', label: '看板', icon: '📊', path: '/' },
  { key: 'pool', label: '股票池', icon: '📋', path: '/pool' },
  { key: 'review', label: '复盘', icon: '🔍', path: '/review' },
  { key: 'limit-up', label: '涨停', icon: '📈', path: '/limit-up' },
]

// 「更多」抽屉: 按分组列全部页面 (与侧栏 4 组一致)
const moreGroups = [
  {
    label: '交易监控',
    items: [
      { key: 'signals', label: '监控看板', icon: '📊', path: '/' },
      { key: 'pool', label: '股票池', icon: '📋', path: '/pool' },
      { key: 'alert-center', label: '预警中心', icon: '🔔', path: '/alert-center' },
      { key: 'models', label: '模型策略', icon: '📖', path: '/models' },
    ],
  },
  {
    label: '市场复盘',
    items: [
      { key: 'limit-up', label: '涨停复盘', icon: '📈', path: '/limit-up' },
      { key: 'popularity', label: '人气分析', icon: '🔥', path: '/popularity' },
      { key: 'alert-overview', label: '预警总览', icon: '📑', path: '/alert-overview' },
    ],
  },
  {
    label: '策略绩效',
    items: [
      { key: 'review', label: '信号复盘', icon: '🔍', path: '/review' },
      { key: 'model-backtest', label: '模型回测', icon: '📊', path: '/model-backtest' },
      { key: 'trade-analysis', label: '交易分析', icon: '💰', path: '/trade-analysis' },
      { key: 'paper-trading', label: '模拟账户', icon: '💹', path: '/paper-trading' },
      { key: 'calculator', label: '仓位计算', icon: '🧮', path: '/calculator' },
    ],
  },
  {
    label: '系统',
    items: [
      { key: 'logs', label: '操作日志', icon: '📝', path: '/logs' },
      { key: 'config', label: '系统设置', icon: '⚙️', path: '/config', admin: true },
      { key: 'users', label: '用户管理', icon: '👤', path: '/users', admin: true },
      { key: 'changelog', label: '版本更新', icon: '📰', path: '/changelog' },
    ],
  },
]

const showMore = ref(false)
const activeTab = computed(() => (route.name as string) || 'signals')
const primaryKeys = primaryTabs.map((t) => t.key)
const moreActive = computed(() => !primaryKeys.includes(activeTab.value))

const visibleGroups = computed(() =>
  moreGroups
    .map((g) => ({ ...g, items: g.items.filter((it) => !it.admin || authStore.isAdmin) }))
    .filter((g) => g.items.length),
)

function go(path: string) {
  showMore.value = false
  if (route.path !== path) router.push(path)
}
</script>

<template>
  <nav class="tab-bar">
    <button
      v-for="tab in primaryTabs"
      :key="tab.key"
      :class="['tab-item', { active: activeTab === tab.key }]"
      :aria-label="tab.label"
      :aria-current="activeTab === tab.key ? 'page' : undefined"
      @click="go(tab.path)"
    >
      <span class="tab-icon" aria-hidden="true">{{ tab.icon }}</span>
      <span class="tab-label">{{ tab.label }}</span>
    </button>
    <button :class="['tab-item', { active: moreActive }]" aria-label="更多" @click="showMore = true">
      <span class="tab-icon" aria-hidden="true">⋯</span>
      <span class="tab-label">更多</span>
    </button>
  </nav>

  <NDrawer v-model:show="showMore" placement="bottom" :height="420" :auto-focus="false">
    <NDrawerContent title="全部功能" closable>
      <div v-for="g in visibleGroups" :key="g.label" class="more-group">
        <div class="more-group-title">{{ g.label }}</div>
        <div class="more-grid">
          <button
            v-for="it in g.items"
            :key="it.key"
            :class="['more-item', { active: activeTab === it.key }]"
            :aria-label="it.label"
            :aria-current="activeTab === it.key ? 'page' : undefined"
            @click="go(it.path)"
          >
            <span class="more-icon" aria-hidden="true">{{ it.icon }}</span>
            <span class="more-label">{{ it.label }}</span>
          </button>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.tab-bar {
  display: flex;
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--surface);
  border-top: 1px solid var(--border);
  z-index: 100;
  padding: 4px 0;
  padding-bottom: env(safe-area-inset-bottom, 4px);
}
.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 0;
  min-height: 44px;
  border: none;
  background: none;
  cursor: pointer;
  touch-action: manipulation;
  color: var(--text2);
  font-size: 10px;
  transition: color 0.15s;
}
.tab-item.active {
  color: var(--primary);
}
.tab-icon {
  font-size: 20px;
  line-height: 1;
}
.tab-label {
  font-size: 10px;
}

.more-group {
  margin-bottom: 16px;
}
.more-group-title {
  font-size: 12px;
  color: var(--text2);
  margin-bottom: 8px;
}
.more-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.more-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 12px 4px;
  border: 1px solid var(--border, #eee);
  border-radius: 8px;
  background: var(--surface);
  cursor: pointer;
  touch-action: manipulation;
  color: var(--text1);
}
.more-item.active {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(9, 105, 218, 0.06);
}
.more-icon {
  font-size: 22px;
  line-height: 1;
}
.more-label {
  font-size: 11px;
}
</style>
