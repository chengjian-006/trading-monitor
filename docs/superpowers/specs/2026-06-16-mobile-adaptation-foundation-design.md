# 移动端适配地基 (Foundation) 设计

- 日期: 2026-06-16
- 状态: 已与用户确认设计, 待写实现计划
- 背景: 全量体检(10 重页面 + 图表组件)发现移动端是"事后补救", 通病为 `useResponsive` 普及率低、断点(720/768/820/900)混乱、宽表挤爆、弹窗撑爆。本期"先打地基, 再批量": 只建可复用基建并用一页验证, 不做全量页面改造。

## 目标与非目标

**目标**
- 统一断点契约(三档), 成为全项目单一真相源。
- 增强 `useResponsive()` 暴露语义布尔量, 现有 `isMobile` 调用零改动。
- 提供 `<ResponsiveTable>` 组件, 把"宽表→移动端降级"系统化。
- 在 `mobile.css` 加全局弹窗兜底, 一次修掉所有弹窗小屏撑爆。
- 规范落档(记忆 + docs), 把约定写死。
- 用 HistoryView 做验证迁移, 证明组件可用并消重。

**非目标(留给批量阶段)**
- 不在本期改造 PoolView/StockTable、矩阵表(ThemeHeat/AlertOverview)、Backtest、Models、Review、图表组件等。这些待地基稳定后逐页套用。

## 设计

### 1. 断点契约(单一真相源)

文件: `frontend/src/composables/useResponsive.ts`

固定三档(不再接受随意断点决定档位):
- `isDesktop`: 宽度 ≥ 1024
- `isTablet`: 768 ≤ 宽度 < 1024
- `isPhone`: 宽度 < 768

兼容约定:
- `isMobile` 保留语义 = `< 768` = `isPhone`。现有所有 `isMobile` 调用(HistoryView/AlertOverview/TradeAnalysis/Backtest 等)零改动。
- 现有 `useResponsive(breakpoint = 768)` 的 `breakpoint` 参数继续被接受以向后兼容(仅影响 `isMobile` 的旧式判断), 但三档布尔量(`isDesktop/isTablet/isPhone`)恒按固定 768/1024 计算, 不受该参数影响。

新增返回:
- 响应式 `width: Ref<number>`
- 导出常量 `export const BREAKPOINTS = { tablet: 768, desktop: 1024 }` 供 JS/组件复用。

返回对象形状:
```ts
{ isDesktop, isTablet, isPhone, isMobile, width }  // 均为 Ref
```

实现要点:
- 单一 `resize` 监听更新 `width`, 三档布尔量为基于 `width` 的 `computed`。
- `onMounted` 注册、`onUnmounted` 注销监听(沿用现有写法)。

CSS 侧断点约定(写进规范, 禁止再写 720/820/900):
- 手机: `@media (max-width: 767.98px)`
- 平板: `@media (min-width: 768px) and (max-width: 1023.98px)`
- 桌面: `@media (min-width: 1024px)`

### 2. `<ResponsiveTable>` 组件

文件: `frontend/src/components/common/ResponsiveTable.vue`

职责: 桌面/平板渲染标准 `NDataTable`; 手机按 `mobileMode` 降级。页面只填数据与列。

Props:
- `columns: DataTableColumn[]` — 完整列定义(桌面/平板用)
- `data: any[]`
- `rowKey?` — 透传 NDataTable
- `loading?`, `maxHeight?` 等常用项透传(按需)
- `mobileMode?: 'card' | 'scroll' | 'columns'` — 默认 `'card'`
- `mobileColumns?: DataTableColumn[]` — `mobileMode='columns'` 时手机用的精简列集; 不传则回退用 `columns` 中标记 `mobilePriority` 的列
- `mobileMax?: number` — 手机卡片/行渲染上限, 默认 300(防无界拉伸, 符合布局护栏)
- `mobileMaxHint?: boolean` — 截断时是否显示"仅展示前 N 条"提示, 默认 true

桌面/平板渲染:
- 标准 `NDataTable`, 默认带 `:resizable-columns="true"`(符合编码规范), 表头粘性由调用方 `maxHeight` 控制。

手机渲染(按 `mobileMode`):
- `'card'`(默认): 每行渲染为卡片。
  - 主路径: 具名插槽 `#card="{ row, index }"` — 调用方完全自定义卡片(等价 HistoryView 现有卡片结构)。
  - 兜底: 未提供 `#card` 插槽时, 按列定义中标记 `mobileLabel`(列标题或自定义标签)与 `mobilePriority` 的列, 自动生成"标签: 值"卡片。`render` 列复用其渲染函数。
  - 卡片列表套统一 CSS 类(提供公用卡片样式 kit), 无固定宽度, `mobileMax` 限量。
- `'scroll'`: 保留 `NDataTable`, 外层套 `overflow-x: auto` 容器, 设置合理 `scroll-x`, 首列 `fixed: 'left'`(适合矩阵型宽表)。
- `'columns'`: 手机仍用表格, 但列集换为 `mobileColumns`(或 `mobilePriority` 列)。

列扩展字段(在 `DataTableColumn` 基础上, 组件内识别, 非 NaiveUI 原生):
- `mobilePriority?: boolean` — 该列在手机精简集/自动卡片中保留
- `mobileLabel?: string` — 自动卡片模式下的字段标签(默认取 `title`)

类型: 组件内定义 `ResponsiveColumn = DataTableColumn & { mobilePriority?: boolean; mobileLabel?: string }`。

### 3. 全局弹窗兜底

文件: `frontend/src/styles/mobile.css`(沿用非侵入式全局覆盖哲学)

在手机档 `@media (max-width: 767.98px)` 内新增:
- `.n-modal`, 模态内的 `.n-card`: `max-width: min(94vw, 560px) !important; width: auto;`
- 模态/抽屉内容体: `max-height: 90vh; overflow-y: auto;`(防内容超长无法关闭)
- `.n-drawer`(底部/侧边抽屉): 合理宽/高约束
- `.n-tooltip`, `.n-popover`: `max-width: 90vw !important;`(修 KLineChart tooltip 超边界类问题)

逃生舱: 个别需要更大宽度的弹窗(如图表弹窗)在该页用 inline 样式或更高优先级选择器覆盖。本兜底只保证"任何弹窗在手机上都不撑爆、可关闭"的下限。

### 4. 规范落档 + 验证迁移

落档:
- 更新记忆 `feedback_mobile-adaptation.md`: 补入三档断点数值、`useResponsive` 新 API、`<ResponsiveTable>` 用法、弹窗兜底约定。
- 本设计文档即 docs 落档。

验证迁移(HistoryView):
- 将 `frontend/src/views/HistoryView.vue` 现有"PC 列集 + 手机列集 + 手工卡片模板 + mobileSignals 限量"四段手工逻辑, 改为使用 `<ResponsiveTable mobileMode="card">` + `#card` 插槽。
- 目标: 行为与现状一致(手机仍是卡片、限量 300、字段分组不变), 但删除重复脚手架, 成为批量阶段的范本。
- 验证标准: 桌面/平板表格不变; 手机卡片展示与迁移前一致; 列宽拖拽、表头粘性正常。

## 影响与风险

- `useResponsive` 改动向后兼容, 风险低; 重点回归现有 4 个 `isMobile` 使用页未变形。
- `<ResponsiveTable>` 为新增组件, 不影响存量页面(仅 HistoryView 主动迁移)。
- 弹窗全局 CSS 为兜底覆盖, 可能影响个别已自适应良好的弹窗——需抽查几个现有弹窗(StockChartsModal/IndexRefModal)确认未被反向压坏。

## 验证方式

- 前端 `npm run build` 通过。
- 手动两端验证(桌面 ≥1024 / 平板 ~800 / 手机 ~375): HistoryView 表格与卡片、抽查 2-3 个弹窗在手机不撑爆且可关闭。
- 不引入新依赖。

## 批量阶段预告(非本期)

地基稳定后, 按严重度逐页套用 `<ResponsiveTable>` 与断点契约: StockTable(核心宽表)、ThemeHeat/AlertOverview 矩阵(scroll 模式)、Backtest、Models、Review、图表组件高度与 tooltip 等。每页改完两端验证, 并按变更日志/部署规范处理。
