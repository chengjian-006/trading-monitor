# 股票池预警标记 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在股票池表格中对当天已触发预警的股票添加明显视觉标记（角标+颜色+摘要栏），并统一全表状态颜色体系。

**Architecture:** 纯前端改动。利用现有 signalStore 数据，在 StockTable.vue 中新增 signalsByCode computed 映射，修改行样式和列渲染逻辑，在表格底部新增可折叠信号摘要组件。移动端 StockList.vue 同步更新。

**Tech Stack:** Vue 3, TypeScript, Naive UI (NDataTable, NCollapse), Pinia

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `frontend/src/components/stock/StockTable.vue` | Modify | 桌面端表格：颜色体系、角标、摘要栏 |
| `frontend/src/components/stock/StockList.vue` | Modify | 移动端卡片：颜色体系、信号标签 |
| `frontend/src/components/stock/SignalSummaryBar.vue` | Create | 可折叠信号摘要栏（桌面+移动复用） |

---

### Task 1: 创建可折叠信号摘要栏组件

**Files:**
- Create: `frontend/src/components/stock/SignalSummaryBar.vue`

- [ ] **Step 1: 创建 SignalSummaryBar.vue**

```vue
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { Signal } from '../../types'

const props = defineProps<{
  signalsByCode: Map<string, Signal[]>
}>()

const collapsed = ref(false)

onMounted(() => {
  const saved = localStorage.getItem('signal-summary-collapsed')
  if (saved === 'true') collapsed.value = true
})

function toggle() {
  collapsed.value = !collapsed.value
  localStorage.setItem('signal-summary-collapsed', String(collapsed.value))
}

function formatTime(dateStr: string) {
  const d = new Date(dateStr)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <div v-if="signalsByCode.size > 0" class="signal-summary-bar">
    <div class="summary-header" @click="toggle">
      <span class="summary-title">
        🔔 今日预警 ({{ signalsByCode.size }}只股票，{{ Array.from(signalsByCode.values()).reduce((n, arr) => n + arr.length, 0) }}个信号)
      </span>
      <span class="summary-toggle">{{ collapsed ? '展开 ▼' : '收起 ▲' }}</span>
    </div>
    <div v-if="!collapsed" class="summary-body">
      <div v-for="[code, signals] in signalsByCode" :key="code" class="summary-row">
        <span class="summary-stock">{{ code }} {{ signals[0].name }}</span>
        <span v-for="s in signals" :key="s.signal_name + s.triggered_at" class="summary-signal-tag">{{ s.signal_name }}</span>
        <span class="summary-time">{{ signals.map(s => formatTime(s.triggered_at || s.time || '')).join(' / ') }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.signal-summary-bar {
  border-top: 1px solid var(--border);
  background: #fffaf5;
  padding: 10px 12px;
  margin-top: 4px;
  border-radius: 0 0 8px 8px;
}
.summary-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
}
.summary-title {
  font-size: 13px;
  font-weight: 600;
  color: #ff6b00;
}
.summary-toggle {
  font-size: 12px;
  color: #999;
  border: 1px solid #ddd;
  padding: 1px 8px;
  border-radius: 3px;
}
.summary-body {
  margin-top: 8px;
}
.summary-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  flex-wrap: wrap;
}
.summary-row:first-child {
  margin-top: 0;
}
.summary-stock {
  font-weight: 600;
  color: #d35400;
  min-width: 100px;
}
.summary-signal-tag {
  background: #ff6b00;
  color: white;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
}
.summary-time {
  color: #888;
  font-size: 11px;
  margin-left: auto;
}
</style>
```

- [ ] **Step 2: 验证文件创建成功**

Run: `ls frontend/src/components/stock/SignalSummaryBar.vue`
Expected: file exists

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/stock/SignalSummaryBar.vue
git commit -m "feat: add collapsible signal summary bar component"
```

---

### Task 2: 更新 StockTable.vue — 统一颜色体系 + 角标 + 摘要栏

**Files:**
- Modify: `frontend/src/components/stock/StockTable.vue`

- [ ] **Step 1: 添加 import 和 signalsByCode computed**

在 `<script setup>` 顶部添加 SignalSummaryBar import，将现有 `signalCodes` 替换为 `signalsByCode`：

```typescript
// 替换原有的:
// const signalCodes = computed(() => new Set(signalStore.signals.map(s => s.code)))

// 新增:
import SignalSummaryBar from './SignalSummaryBar.vue'

const signalsByCode = computed(() => {
  const map = new Map<string, typeof signalStore.signals>()
  for (const s of signalStore.signals) {
    if (!map.has(s.code)) map.set(s.code, [])
    map.get(s.code)!.push(s)
  }
  return map
})
```

- [ ] **Step 2: 更新代码列 render — 添加角标**

修改 columns computed 中 `key: 'code'` 列的 render 函数：

```typescript
{
  title: '代码',
  key: 'code',
  width: 80,
  render: (row: Stock) => {
    const isFocused = !!row.focused
    const isHold = row.status === 'hold'
    const color = isFocused ? '#e63946' : '#2080f0'
    const fontWeight = (isFocused || isHold) ? 700 : 'normal'
    const signals = signalsByCode.value.get(row.code)
    const children: any[] = [h('span', { style: { fontFamily: 'monospace', color, fontWeight } }, row.code)]
    if (signals) {
      children.push(h('span', {
        style: {
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginLeft: '2px',
          width: '16px',
          height: '16px',
          borderRadius: '50%',
          background: '#ff3b00',
          color: 'white',
          fontSize: '10px',
          fontWeight: '700',
          verticalAlign: 'top',
        }
      }, String(signals.length)))
    }
    return h('span', { style: { whiteSpace: 'nowrap' } }, children)
  },
},
```

- [ ] **Step 3: 更新名称列 render — 颜色体系**

修改 columns computed 中 `key: 'name'` 列的 render 函数：

```typescript
{
  title: '名称',
  key: 'name',
  width: 120,
  ellipsis: { tooltip: true },
  render: (row: Stock) => {
    const isFocused = !!row.focused
    const isHold = row.status === 'hold'
    const hasSignal = signalsByCode.value.has(row.code)
    // 字体颜色优先级: 关注红 > 持仓蓝 > 信号橙 > 默认
    const color = isFocused ? '#e63946' : isHold ? '#1a56a8' : hasSignal ? '#d35400' : 'inherit'
    const fontWeight = (isFocused || isHold || hasSignal) ? 700 : 'normal'
    const prefix = isFocused ? '*' : ''
    if (prefix) {
      return h('span', { style: { position: 'relative', paddingLeft: '0', color, fontWeight } }, [
        h('span', { style: { position: 'absolute', right: '100%', marginRight: '2px' } }, prefix),
        row.name,
      ])
    }
    return h('span', { style: { color, fontWeight } }, row.name)
  },
},
```

- [ ] **Step 4: 更新 row-props — 背景色和左边框**

修改 template 中 NDataTable 的 `:row-props`：

```typescript
:row-props="(row: Stock) => {
  const styles: string[] = []
  const isHold = row.status === 'hold'
  const hasSignal = signalsByCode.has(row.code)
  // 背景色优先级: 持仓蓝 > 信号橙
  if (isHold) {
    styles.push('background: rgba(32, 128, 240, 0.08)')
  } else if (hasSignal) {
    styles.push('background: rgba(255, 120, 0, 0.12)')
  }
  // 信号左边框始终显示
  if (hasSignal) {
    styles.push('border-left: 4px solid #ff6b00')
  }
  return styles.length ? { style: styles.join(';') } : {}
}"
```

- [ ] **Step 5: 在模板中添加 SignalSummaryBar**

将 template 中的 `<div>` 包裹修改为：

```html
<template>
  <div>
    <NDataTable
      :columns="columns"
      :data="stocks"
      :bordered="false"
      size="small"
      :row-key="(row: Stock) => row.code"
      :scroll-x="1310"
      :row-props="(row: Stock) => {
        const styles: string[] = []
        const isHold = row.status === 'hold'
        const hasSignal = signalsByCode.has(row.code)
        if (isHold) {
          styles.push('background: rgba(32, 128, 240, 0.08)')
        } else if (hasSignal) {
          styles.push('background: rgba(255, 120, 0, 0.12)')
        }
        if (hasSignal) {
          styles.push('border-left: 4px solid #ff6b00')
        }
        return styles.length ? { style: styles.join(';') } : {}
      }"
      @update:sorter="handleSorterChange"
    />
    <SignalSummaryBar :signals-by-code="signalsByCode" />
  </div>
</template>
```

- [ ] **Step 6: 验证编译无误**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/stock/StockTable.vue
git commit -m "feat: add signal badge, color scheme, and summary bar to stock table"
```

---

### Task 3: 更新 StockList.vue — 移动端同步颜色体系 + 信号标签

**Files:**
- Modify: `frontend/src/components/stock/StockList.vue`

- [ ] **Step 1: 添加 signalsByCode computed 和 SignalSummaryBar import**

替换现有 `signalCodes` computed：

```typescript
import SignalSummaryBar from './SignalSummaryBar.vue'

// 替换:
// const signalCodes = computed(() => new Set(signalStore.signals.map(s => s.code)))

const signalsByCode = computed(() => {
  const map = new Map<string, typeof signalStore.signals>()
  for (const s of signalStore.signals) {
    if (!map.has(s.code)) map.set(s.code, [])
    map.get(s.code)!.push(s)
  }
  return map
})
```

- [ ] **Step 2: 更新 template — 卡片 class 和颜色**

修改卡片的 class 绑定和样式：

```html
<template>
  <div class="stock-list">
    <div v-for="s in stocks" :key="s.code"
      :class="['stock-card', {
        focused: s.focused,
        signaled: signalsByCode.has(s.code),
        hold: s.status === 'hold'
      }]"
    >
      <div class="stock-top">
        <div class="stock-top-left">
          <span v-if="s.focused" class="stock-star">*</span>
          <span class="stock-name" :style="{ color: s.focused ? '#e63946' : s.status === 'hold' ? '#1a56a8' : signalsByCode.has(s.code) ? '#d35400' : 'inherit' }">{{ s.name }}</span>
          <span class="stock-code" :style="{ color: s.focused ? '#e63946' : '#2080f0' }">{{ s.code }}</span>
          <span v-if="signalsByCode.has(s.code)" class="signal-badge">{{ signalsByCode.get(s.code)!.length }}</span>
        </div>
        <NTag size="small" :type="s.trade_type === 'short' ? 'info' : 'warning'" :bordered="false">
          {{ s.trade_type === 'short' ? '短线' : '中线' }}
        </NTag>
      </div>
      <div class="stock-middle">
        <span class="stock-price">{{ s.price != null ? s.price.toFixed(2) : '-' }}</span>
        <span
          v-if="s.pct_change != null"
          :class="['stock-pct', s.pct_change >= 0 ? 'up' : 'down']"
        >
          {{ s.pct_change >= 0 ? '+' : '' }}{{ s.pct_change.toFixed(2) }}%
        </span>
        <span v-if="s.speed != null && s.speed !== 0" :class="['stock-pct', s.speed >= 0 ? 'up' : 'down']" style="font-size: 12px;">
          涨速{{ s.speed >= 0 ? '+' : '' }}{{ s.speed.toFixed(2) }}%
        </span>
      </div>
      <!-- 信号标签行 -->
      <div v-if="signalsByCode.has(s.code)" class="stock-signals">
        <span v-for="sig in signalsByCode.get(s.code)" :key="sig.signal_name + sig.triggered_at" class="signal-tag">{{ sig.signal_name }}</span>
      </div>
      <div v-if="s.industry || s.amount" class="stock-extra">
        <span v-if="s.industry" class="stock-industry">{{ s.industry }}</span>
        <span v-if="s.amount" class="stock-amount">{{ formatAmount(s.amount) }}</span>
      </div>
      <div class="stock-bottom">
        <NTag size="tiny" :type="s.status === 'hold' ? 'success' : 'default'" :bordered="false">
          {{ s.status === 'hold' ? '持仓' : '观察' }}
        </NTag>
        <div style="display: flex; gap: 8px">
          <NButton size="small" :type="s.focused ? 'warning' : 'primary'" :secondary="!s.focused" @click="handleToggleFocus(s)">
            <template #icon><NIcon><component :is="s.focused ? Star : StarOutline" /></NIcon></template>
            {{ s.focused ? '已关注' : '关注' }}
          </NButton>
          <NButton size="small" type="error" secondary @click="handleDelete(s.code, s.name)">
            <template #icon><NIcon><TrashOutline /></NIcon></template>
            删除
          </NButton>
        </div>
      </div>
    </div>
    <SignalSummaryBar :signals-by-code="signalsByCode" />
    <div v-if="stocks.length === 0" class="empty">暂无股票</div>
  </div>
</template>
```

- [ ] **Step 3: 更新 style — 新增信号相关样式**

在 `<style scoped>` 中修改 `.signaled` 样式并添加新样式：

```css
.stock-card.signaled {
  background: rgba(255, 120, 0, 0.12);
  border-color: rgba(255, 120, 0, 0.4);
  border-left: 4px solid #ff6b00;
}
.stock-card.hold {
  background: rgba(32, 128, 240, 0.08);
  border-color: rgba(32, 128, 240, 0.2);
}
.stock-card.hold.signaled {
  background: rgba(32, 128, 240, 0.08);
  border-left: 4px solid #ff6b00;
}
.stock-top-left {
  display: flex;
  align-items: center;
  gap: 4px;
}
.signal-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #ff3b00;
  color: white;
  font-size: 10px;
  font-weight: 700;
  margin-left: 4px;
}
.stock-signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}
.signal-tag {
  background: #ff6b00;
  color: white;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
}
```

- [ ] **Step 4: 验证编译无误**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/stock/StockList.vue
git commit -m "feat: add signal marks and color scheme to mobile stock list"
```

---

### Task 4: 浏览器验证

- [ ] **Step 1: 启动开发服务器**

Run: `cd frontend && npm run dev`

- [ ] **Step 2: 浏览器验证桌面端**

在浏览器访问股票池页面，检查：
- 普通股票行无特殊样式
- 重点关注的股票：红色字体 `#e63946`，代码和名称加粗，名称前有 `*`
- 持仓股票：蓝色淡底，蓝色字体加粗
- 有信号的股票：橙色淡底，4px 左边框，代码右侧红色圆形角标，名称橙色加粗
- 持仓+信号：蓝色淡底 + 左边框 + 角标（蓝底不被橙底覆盖）
- 关注+信号：橙色淡底 + 左边框 + 角标 + 红色字体
- 表格底部可折叠信号摘要栏：显示预警股票数/信号数，展开后列出具体信号名和时间
- 点击收起后刷新页面，仍保持收起状态

- [ ] **Step 3: 浏览器验证移动端**

将浏览器窗口缩窄至移动端宽度，检查：
- 卡片颜色体系与桌面端一致
- 有信号的卡片显示信号标签行
- 角标显示在代码旁
- 底部信号摘要栏正常显示

- [ ] **Step 4: Final commit (if any fixes needed)**

```bash
git add -u
git commit -m "fix: polish signal mark styling after browser verification"
```
