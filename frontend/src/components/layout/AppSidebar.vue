<script setup lang="ts">
import { ref, computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  PulseOutline, LayersOutline, TimeOutline,
  ReaderOutline, SettingsOutline, PeopleOutline,
  OptionsOutline, RocketOutline, TrendingUpOutline,
  TimerOutline, BarChartOutline,
  WalletOutline, ListOutline, SearchOutline,
} from '@vicons/ionicons5'
import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

interface MenuItem {
  key: string
  label: string
  path: string
  icon: Component
  admin?: boolean
}

interface MenuGroup {
  key: string
  label: string
  icon: string
  children: MenuItem[]
}

const menuGroups: MenuGroup[] = [
  {
    key: 'trading',
    label: '交易监控',
    icon: '📊',
    children: [
      { key: 'signals', label: '监控看板', path: '/', icon: PulseOutline },
      { key: 'pool', label: '股票池', path: '/pool', icon: LayersOutline },
      { key: 'wencai', label: '问财候选榜', path: '/wencai', icon: SearchOutline },
      { key: 'history', label: '信号历史', path: '/history', icon: TimeOutline },
      { key: 'models', label: '模型图鉴', path: '/models', icon: ReaderOutline },
      { key: 'signal-config', label: '策略配置', path: '/signal-config', icon: OptionsOutline },
    ],
  },
  {
    key: 'review',
    label: '复盘',
    icon: '🔍',
    children: [
      { key: 'win-rate', label: '买卖胜率', path: '/review', icon: TrendingUpOutline },
      { key: 'alert-overview', label: '预警总览', path: '/alert-overview', icon: ListOutline },
      { key: 'popularity', label: '人气分析', path: '/popularity', icon: TrendingUpOutline },
      { key: 'model-backtest', label: '模型回测', path: '/model-backtest', icon: BarChartOutline },
      { key: 'trade-analysis', label: '交易分析', path: '/trade-analysis', icon: WalletOutline },
      { key: 'paper-trading', label: '模拟账户', path: '/paper-trading', icon: BarChartOutline },
    ],
  },
  {
    key: 'system',
    label: '系统管理',
    icon: '⚙️',
    children: [
      { key: 'logs', label: '操作日志', path: '/logs', icon: ReaderOutline },
      { key: 'config', label: '系统设置', path: '/config', icon: SettingsOutline, admin: true },
      { key: 'scheduled-tasks', label: '定时任务', path: '/scheduled-tasks', icon: TimerOutline, admin: true },
      { key: 'users', label: '用户管理', path: '/users', icon: PeopleOutline, admin: true },
      { key: 'lark-templates', label: '推送模版', path: '/lark-templates', icon: ListOutline, admin: true },
      { key: 'changelog', label: '版本更新', path: '/changelog', icon: RocketOutline },
    ],
  },
]

const expandedGroups = ref<Record<string, boolean>>({
  trading: true,
  review: true,
  system: true,
})

function toggleGroup(groupKey: string) {
  expandedGroups.value[groupKey] = !expandedGroups.value[groupKey]
}

const visibleGroups = computed(() =>
  menuGroups
    .map((g) => ({
      ...g,
      children: g.children.filter((item) => !item.admin || authStore.isAdmin),
    }))
    .filter((g) => g.children.length > 0),
)

const activeKey = computed(() => (route.name as string) || 'signals')
</script>

<template>
  <aside class="sidebar">
    <div v-for="group in visibleGroups" :key="group.key" class="menu-group">
      <div class="group-header" role="button" tabindex="0"
           :aria-expanded="expandedGroups[group.key]"
           @click="toggleGroup(group.key)" @keydown.enter="toggleGroup(group.key)">
        <span :class="['group-arrow', { expanded: expandedGroups[group.key] }]" aria-hidden="true">▶</span>
        <span class="group-icon" aria-hidden="true">{{ group.icon }}</span>
        <span class="group-label">{{ group.label }}</span>
      </div>
      <div :class="['group-children', { collapsed: !expandedGroups[group.key] }]"
           :style="{ maxHeight: expandedGroups[group.key] ? group.children.length * 42 + 'px' : '0' }">
        <a v-for="item in group.children"
           :key="item.key"
           :class="['menu-item', { active: activeKey === item.key }]"
           role="link" tabindex="0"
           :aria-current="activeKey === item.key ? 'page' : undefined"
           @click="router.push(item.path)"
           @keydown.enter="router.push(item.path)">
          <NIcon :component="item.icon" :size="15" class="menu-item-icon" aria-hidden="true" />
          {{ item.label }}
        </a>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  box-shadow: var(--sidebar-shadow);
  flex-shrink: 0;
  overflow-y: auto;
  padding: 12px 0;
  position: sticky;
  top: var(--navbar-height);
  align-self: flex-start;
  height: calc(100vh - var(--navbar-height));
  z-index: 10;
}

.menu-group {
  margin-bottom: 6px;
}

.group-header {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.85);
  cursor: pointer;
  user-select: none;
  touch-action: manipulation;
  transition: background 0.15s;
}
.group-header:hover {
  background: rgba(46, 158, 255, 0.06);
}

.group-arrow {
  display: inline-block;
  width: 16px;
  height: 16px;
  margin-right: 6px;
  font-size: 10px;
  transition: transform 0.2s;
  text-align: center;
  line-height: 16px;
  color: #999;
}
.group-arrow.expanded {
  transform: rotate(90deg);
}

.group-icon {
  margin-right: 8px;
  display: inline-flex;
  align-items: center;
  font-size: 16px;
}

.group-children {
  overflow: hidden;
  transition: max-height 0.25s ease;
}
.group-children.collapsed {
  max-height: 0 !important;
}

.menu-item {
  display: flex;
  align-items: center;
  padding: 9px 12px 9px 36px;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.55);
  text-decoration: none;
  cursor: pointer;
  touch-action: manipulation;
  transition: all 0.15s;
  border-left: 3px solid transparent;
}
.group-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.menu-item-icon {
  margin-right: 6px;
  flex-shrink: 0;
}
.menu-item:hover {
  background: rgba(46, 158, 255, 0.06);
  color: rgba(0, 0, 0, 0.85);
}
.menu-item.active {
  background: var(--sidebar-active-bg);
  color: var(--sidebar-active-color);
  border-left-color: var(--sidebar-active-color);
  font-weight: 600;
}
</style>
