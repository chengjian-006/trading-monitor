# 观潮 · 手机端设计基线（mobile-design-baseline）

> 配套文档：`design-baseline.md`（PC/全站 UI 基线）、`push-design-baseline.md`（推送）。
> 本文只写**手机端相对 PC 的差异与新增规范**，其余一律继承 `design-baseline.md`，不重复。

---

## 0. 地位与复用原则

**一条总原则：手机端不是另起一套设计，而是同一套设计的「窄屏形态」。**

- **完全复用 PC 基线**：设计 Token（色板/字号阶梯/间距/圆角/字重/阴影）、A股语义色（红涨绿跌、up/down 与 success/danger 分离）、9 型页面taxonomy、交互与操作标准（加载分流/反馈去toast化/表格五规/确认防误触）——**这些跨端一致，手机端零改动直接用**。
- **手机端只重定义三件事**：①**布局**（多列→单列、不等宽栅格→回落单列）②**导航**（顶栏侧栏 → 底部 Tab 栏）③**交互密度**（触摸目标放大、渐进披露、宽表滚动/卡片化）。
- **单一真相源**：断点走 `useResponsive`（`BREAKPOINTS`），CSS 走全局 `src/styles/mobile.css` + 组件 scoped `@media`。不允许随手写 720/820/900 等杂断点。

---

## 1. 断点契约（唯一）

来源：`src/composables/useResponsive.ts`（JS 侧）+ `@media`（CSS 侧），二者断点必须一致。

| 档 | 宽度 | JS | CSS |
|---|---|---|---|
| 手机 phone | `< 768` | `isPhone` / `isMobile` | `@media (max-width: 768px)` |
| 平板 tablet | `768–1023` | `isTablet` | `@media (768px ≤ w < 1024px)` |
| 桌面 desktop | `≥ 1024` | `isDesktop` | `@media (min-width: 1024px)` |

- 需要结构性切换（表↔卡片、显隐、列数）→ 用 JS 的 `isPhone`。
- 纯样式微调（间距/字号/堆叠）→ 用 CSS `@media`。
- **`zoom` 只作用桌面**（`@media (min-width:769px) body{zoom:1.1}`）；手机端不叠 zoom，字号按真实值。

---

## 2. 复用 vs 重定义（一张表看全）

| 维度 | 手机端处理 | 说明 |
|---|---|---|
| 色板 / 语义色 | **复用** | 同 tokens.css，红涨绿跌不变 |
| 字号阶梯 | **复用**（不叠 zoom） | 正文最小 13px 保证可读 |
| 间距 / 圆角 / 阴影 | **复用** | 卡片内边距可收一档 |
| 组件底座（Naive UI） | **复用** | 尺寸走 size="small" 的既有约定 |
| 交互标准（加载/反馈/确认） | **复用** | toast 用 `useGlobalMessage`，确认用 `NPopconfirm` |
| **页面布局** | **重定义** | 多列 grid/flex → 单列堆叠 |
| **导航** | **重定义** | 侧栏 `AppSidebar` → 底栏 `AppTabBar`（4 主 + 更多抽屉） |
| **宽表** | **重定义** | 横向滚动 或 卡片化（见 §3.2） |
| **查询区** | **重定义** | 默认折叠 `FilterPanel`（见 §3.3） |
| **触摸目标** | **重定义** | ≥ 44px（见 §3.1） |

---

## 3. 手机端特有规范（硬规范）

### 3.1 触摸目标 ≥ 44px
参考 Apple HIG（44×44pt）/ Material（48dp）。本项目取 **≥ 44px**（紧凑场景不低于 40px）。
- 所有可点：按钮、开关、Tab、胶囊、图标按钮、表格行操作。
- 全局 `mobile.css` 已把 NButton/NInput 提到紧凑高度；结构性按钮在 scoped `@media` 里补 `min-height: 44px` + `flex:1` 全宽/均分。

### 3.2 宽表两条路（二选一，不允许硬挤）
- **路 A · 横向滚动**（默认、最省）：全局 `mobile.css` 已对所有 `NDataTable` 强制 `overflow-x:auto` + `min-width:max-content`——**NDataTable 天然滚动，无需每页加 `:scroll-x`**。原生 `<table>` 必须自己套 `overflow-x:auto` 容器。
- **路 B · 卡片化**（高频/核心列表）：`isPhone` 时把宽表换成竖向卡片列表（样板：股票池 `StockTable`↔`StockList`）。每张卡片：主标的 + 关键数值上移、次要信息下沉。
- 判据：日常高频看的核心列表走卡片化；低频/管理类表走横滚。

### 3.3 查询区默认折叠（渐进披露）
参考主流 App「筛选」交互：手机屏小，查询表单常展开会占满首屏、把内容顶到看不见。
- **规范**：所有查询区用 `FilterPanel` 组件包裹 → **桌面照常展开；手机端默认折叠，顶部「筛选」开关点开**。
- 组件：`src/components/common/FilterPanel.vue`。用法：`<FilterPanel><div class="filter-bar">…</div></FilterPanel>`。
- **合规变体（允许不折叠）**：①**搜索优先页**（如股票池，搜索框是核心导航）——保留搜索常显，把「高级筛选」做成页内渐进披露即可；②**紧凑控件行**（如预警总览的 天数/分组/方向，仅 2–3 个 select，手机端 flex-wrap 全宽即可，不构成占屏大表单）。判据：只有「多字段、会占满首屏」的查询表单才必须用 FilterPanel 折叠。

### 3.4 布局：内容优先 + 单列堆叠
- 不等宽双栏栅格（如盯盘看板 1.62fr/1fr）在手机端一律 `grid-template-columns: 1fr` 回落单列。
- **最重要的信息排最前**（大盘风险/情绪/持仓浮盈在上，明细在下）。
- 横排的「标签 + 控件」「信息 + 操作按钮」在手机端 `flex-wrap:wrap` 换行或纵向堆叠，控件全宽。

### 3.5 导航：底部 Tab 栏
参考 Material bottom navigation / iOS tab bar。
- `AppTabBar`：底部固定 **4 个主入口 + 「更多」抽屉**（抽屉按侧栏同款分组列全部）。
- 拇指热区：主操作放屏幕下半可达区；破坏性操作远离误触区。
- 安全区：底栏 `padding-bottom: env(safe-area-inset-bottom)`。

### 3.6 弹层与输入
- `NDatePicker`/`NSelect` 下拉 `to="body"` + 手机端全宽，防被裁剪/错位。
- 输入框按语义给 `type`（`search`/`tel`/`number`）触发合适软键盘。
- 手机端**慎用 sticky**：查询区/头部 sticky 在窄屏易遮挡内容，`@media` 里改 `position:static`。

---

## 4. 通用控件与友好设计（跨端复用 + 主流实践）

对标 Material Design 3 / Apple HIG / Ant Design Mobile，沉淀为跨端复用件：

| 控件 / 模式 | 用途 | 本项目落地 |
|---|---|---|
| 底部导航 | 主入口切换 | `AppTabBar` |
| 折叠筛选（渐进披露） | 收纳次要操作 | `FilterPanel` / `NCollapse` |
| 骨架屏 | 加载占位（>300ms） | `NSkeleton`，全站统一 |
| 空态 | 无数据友好引导 | `NEmpty` + 一句「怎么才有数据」 |
| Toast 反馈 | 操作结果轻提示 | `useGlobalMessage`（禁 useMessage 无provider） |
| 确认防误触 | 删除/重置等 | `NPopconfirm` |
| 全宽主按钮 | 手机端主操作 | `@media` 下 `flex:1` / `width:100%` |
| 卡片列表 | 宽表的手机形态 | `StockList` 式卡片 |
| 下拉刷新 | 列表刷新（可选） | 暂用显式「刷新」按钮，未来可上 pull-to-refresh |

**友好设计清单**：一屏一焦点；关键数字大而醒目（mono 等宽）；红涨绿跌一眼分辨；错误文案说清「怎么修」不只报错；破坏性操作二次确认；触摸有即时反馈。

---

## 5. 组件底座（手机端复用件）

| 件 | 路径 | 职责 |
|---|---|---|
| `useResponsive` | composables/ | 断点单一真相源（isPhone/isTablet/isDesktop） |
| `mobile.css` | styles/ | 全局手机端规则（NDataTable 横滚/表单堆叠/按钮紧凑等） |
| `FilterPanel` | components/common/ | 查询区手机端折叠容器 |
| `AppTabBar` | components/layout/ | 底部 Tab 导航 + 更多抽屉 |
| `StockList` | components/stock/ | 宽表的手机卡片形态样板 |

---

## 6. 新页手机端自检清单（每个页面/PR 必过）

- [ ] 手机宽度（≤414px）无横向 body 溢出（`overflow-x` 只在宽内容自己的容器上）
- [ ] 宽表：走了横滚 或 卡片化（NDataTable 天然滚；原生 table 套 overflow-x）
- [ ] 查询区：用 `FilterPanel` 包了、默认折叠
- [ ] 多列布局：手机端回落单列
- [ ] 触摸目标 ≥ 44px（按钮/开关/Tab/行操作）
- [ ] 正文字号 ≥ 13px 可读；不叠 zoom
- [ ] sticky 不遮挡内容；底栏不挡最后一行
- [ ] 弹层（日期/下拉）`to="body"` 且全宽不裁剪
- [ ] 断点用 768/1024，走 `useResponsive`，无杂断点

---

## 7. 现状与欠账（2026-07-18）

- ✅ 已落地：断点契约、mobile.css 全局、FilterPanel 折叠（7 页）、AppTabBar 底栏、股票池表↔卡片、多数数据页 @media、触摸目标放大、登录页/推送模版页溢出修复。
- ⏳ 可继续：更多高频列表卡片化（交易分析目前靠全局横滚，可评估卡片化）、下拉刷新、平板中间态（768–1023）精修。
