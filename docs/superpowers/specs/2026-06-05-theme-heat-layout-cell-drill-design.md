# 市场情绪温度表：榜单右移 + 单元格下钻涨停个股

日期：2026-06-05
范围：纯前端，单文件 `frontend/src/components/common/ThemeHeatPanel.vue`
后端：无改动（数据已具备）

## 背景

`ThemeHeatPanel.vue`（市场情绪温度表）当前自上而下为四段：

1. 当前主线榜 chips（`.mainline`）
2. 日期 × 题材 涨停家数矩阵（`.matrix-wrap`）
3. 强势 / 热点板块详细榜 + 自选联动（`.rank`，section B）

数据来自 `GET /api/market-report/theme-heat?days=15` → `cfzy_sys_theme_heat`。
每个单元格 `cell(d, theme)` = `{ c: 涨停家数, s: "代表股名,..." }`，`s` 为题材当日代表股**名称**（逗号分隔，约 ≤8 个），目前仅出现在 hover 提示（`cellTitle`，L42-46）。

## 目标

1. **榜单右移**：把强势/热点板块榜（section B）从矩阵下方移到矩阵**右侧**，形成左矩阵 / 右榜单两栏。
2. **单元格下钻**：点击矩阵中非空数字，在矩阵下方展开该单元格的涨停个股。

## 数据现状与约束

- 下钻所需数据已在响应里（`cell(d, theme).s`），无需后端改动。
- `s` 只有**股票名称、无代码、上限约 8 个**。因此：
  - 展开的个股默认以纯文本展示。
  - 名称命中自选池（`stockStore.stocks` 按 `name` 匹配）的，渲染为可点击项，复用 `ui.openStock(code, name)` 打开个股详情；其余为纯文本。
  - 若 `c` 大于实际代表股个数，详情条注明「代表股，非全量」，避免误读为全部涨停股。

## 设计

### 1. 两栏布局（榜单右移）

新增包裹层 `.body`，把矩阵区与榜单区并列：

```
<div class="body">
  <div class="left">
    <div class="matrix-wrap"> …矩阵… </div>
    <div v-if="activeCell" class="cell-detail"> …下钻详情条… </div>   <!-- 见 2 -->
  </div>
  <div class="rank"> …强势/热点板块榜（section B 原样迁移）… </div>
</div>
```

- `.body { display: flex; gap: 12px; align-items: flex-start; }`
- `.left { flex: 1; min-width: 0; }`（`min-width:0` 保证内部矩阵横向滚动不撑破 flex）
- `.rank { width: 340px; flex-shrink: 0; }`，去掉原 `margin-top / border-top`，改为左侧竖分隔（`border-left: 1px dashed #eee; padding-left: 12px;`）。
- **当前主线榜 chips（`.mainline`）保持在 `.body` 之上**，仍是整宽，不进两栏。
- **响应式（窄屏自动堆叠）**：`@media (max-width: 900px)` 下 `.body { flex-direction: column; }`，`.rank { width: auto; border-left: none; padding-left: 0; border-top: 1px dashed #eee; margin-top: 12px; padding-top: 8px; }`，即回退到当前上下排列，矩阵与榜单都不被挤压。

榜单内部 `.rk-row` 现为 `flex-wrap` 横排；在 340px 窄栏中会自动换行，可读性可接受，本期不重排。

### 2. 单元格下钻（点击数字展开涨停个股）

状态：`const activeCell = ref<{ d: string; theme: string } | null>(null)`。

- 单元格点击：`onCellClick(d, theme)`
  - 若 `cell(d, theme)?.c` 为空 → 忽略（空格不可点）。
  - 否则切换：再次点击同一格则收起（`activeCell = null`），点别的格则切换目标。
- 视觉：非空 `.cell` 加 `cursor: pointer`；当前 `activeCell` 对应格加高亮（复用既有 `.col-hl` 风格的 outline，或新增 `.cell-active`）。
- 详情条 `.cell-detail`（位于矩阵正下方、`.left` 内）内容：
  - 标题：`{{ fmtDate(d) }} · {{ theme }} · {{ c }}只涨停`
  - 个股列表：`s` 按逗号拆分逐个渲染；命中自选者可点击（`ui.openStock`），其余纯文本。
  - 当 `splitNames.length < c` 时追加灰字「· 仅代表股」。
  - 关闭：条内提供「×」，或点同格收起。

辅助函数：

```ts
function onCellClick(d: string, theme: string) {
  if (!cell(d, theme)?.c) return
  const cur = activeCell.value
  activeCell.value = cur && cur.d === d && cur.theme === theme ? null : { d, theme }
}
function cellStocks(d: string, theme: string): { name: string; code?: string }[] {
  const s = cell(d, theme)?.s || ''
  return s.split(/[,，]/).map(n => n.trim()).filter(Boolean).map(name => {
    const hit = stockStore.stocks.find(x => x.name === name)
    return hit ? { name, code: hit.code } : { name }
  })
}
```

矩阵刷新（每 180s `load()`）后，若 `activeCell` 指向的题材列已不在前 12 列或该日无数据，详情条对应 `cell()` 返回空，自然不渲染——无需额外清理，但为稳妥可在 `load()` 成功后校验并清空失效的 `activeCell`。

## 样式新增（要点）

```css
.body { display: flex; gap: 12px; align-items: flex-start; margin-top: 6px; }
.left { flex: 1; min-width: 0; }
.rank { width: 340px; flex-shrink: 0; border-left: 1px dashed #eee; padding-left: 12px; }
.cell.clickable { cursor: pointer; }
.cell-active { outline: 2px solid #cf222e; outline-offset: -2px; }
.cell-detail { margin-top: 8px; padding: 6px 8px; background: #fafafa; border: 1px solid #eee; border-radius: 4px; font-size: 12px; }
.cell-detail .cd-title { font-weight: 600; margin-right: 8px; }
.cell-detail .cd-stock { margin-right: 8px; }
.cell-detail .cd-stock.linked { color: #2e9eff; cursor: pointer; }
.cell-detail .cd-stock.linked:hover { text-decoration: underline; }
@media (max-width: 900px) {
  .body { flex-direction: column; }
  .rank { width: auto; border-left: none; padding-left: 0; border-top: 1px dashed #eee; margin-top: 12px; padding-top: 8px; }
}
```

（`.rank` 原 `margin-top:12px; border-top` 移入媒体查询，桌面态用 `border-left`。）

## 不做（YAGNI）

- 不为下钻补全量涨停股（后端 `sample_codes` 上限不改）。
- 不给下钻个股做按代码反查/拉行情。
- 不重排榜单 `.rk-row` 的内部布局以适配窄栏。
- 不动主线榜、矩阵配色、热度分算法。

## 验证

- 桌面宽屏：矩阵居左、榜单居右、对齐顶部；点任意非空数字→下方出现该格涨停个股，再点收起，点别的格切换。
- 命中自选的个股可点开详情；非自选为纯文本；`仅代表股` 提示在样本不足时出现。
- 窗口缩到 <900px：两栏堆叠回上下，矩阵保留横向滚动，榜单完整。
- 空数据 / 加载骨架态不受影响。

## 交付

- 改 `ThemeHeatPanel.vue`（模板 + script + style）。
- `frontend/src/data/changelog.ts` 顶部加版本记录（按项目约定）。
- 部署云端（按自动部署约定）。
