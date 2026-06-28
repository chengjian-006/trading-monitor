# 区间复盘清单 — 设计方案

- 日期: 2026-06-07
- 状态: 设计已确认, 待写实施计划
- 范围: 单一功能, 一个实施计划即可覆盖

## 1. 背景与目标

现有 `/review` 复盘页与 `/backtest` 回测页提供的都是**聚合统计**视角:
按 signal_id 聚合的胜率(`get_signal_outcome_stats`)、买卖点对比(`get_outcome_compare`)、
逐日成功率曲线(`get_signal_perf_stats`), 以及回测页的重模拟/参数网格。

缺少的视角: **"某个时间区间内, 系统具体触发了哪几个买卖点, 各自盈亏路径如何"** 的逐条明细清单。
这正是手工复盘最常用的视角(本次会话用临时脚本 `_review_recent_buys.py` 跑过一次, 现要产品化)。

**目标**: 在 `/review` 页新增「区间复盘清单」卡片, 用户自主选择时间区间, 查看该区间内触发的
个股买卖点/减仓信号逐条明细 + 当前盈亏/路径/固定持有收益/评估结论, 并可按类型汇总、导出 xlsx。

## 2. 用户决策(已确认)

| 项 | 决策 |
|---|---|
| 功能位置 | `/review` 复盘页顶部新增一张卡片, 置于原有聚合卡之上 |
| 区间选择 | 预设按钮(近5日/近2周/近1月/近3月) + 自定义起止日期(NDatePicker range) |
| 信号范围 | 可勾选类别过滤, 默认勾选 买点+卖点+减仓; 板块预警、大盘风控 可选 |
| 收益口径 | 全要: 当前收益 + 区间最大浮盈/浮亏 + 固定持有 T+1/T+3/T+5 + 评估结论 |
| 卖点口径 | 个股触发后常**无真实 SELL_ 信号**(实测某窗口12只票全历史0条卖点); 故卖点取**买点自带「交易计划」的计划性出场规则**, 解析为 止盈价/止损价/减仓动作/时停日, 并标注路径是否触及, **只列目标价不做收益仿真**(用户确认) |
| 后端取数 | 方案A 混合读取(perf 冻结表骨架 + kline 最新收盘叠"当前收益") |
| 导出格式 | 真 xlsx(前端引入 sheetjs/xlsx 库), 带表头/列宽/盈亏底色 |

## 3. 架构与数据流

```
前端 ReviewView 新卡片
  筛选栏(区间预设/起止日期/类别多选) ──GET /api/review/signals?start=&end=&categories=──▶
                                                                                  │
后端 routers/review(或 backtest).py ── repo.get_review_signal_list(user_id,start,end,categories) ──▶
                                                                                  │
  MySQL:                                                                          │
   cfzy_biz_signals (区间+类别过滤)                                                │
     LEFT JOIN 聚合(cfzy_biz_signal_perf)  → 区间最大浮盈/浮亏 + T+1/3/5 + 最新close_pct │
     读 outcome / outcome_p1/p3/p5 字段     → 评估结论 + 固定持有收益(冗余兜底)         │
     叠加 cfzy_sys_kline_cache 最新收盘      → 当前收益(活列)                          │
                                                                                  ▼
  返回 { rows:[逐条], summary:[按signal_id汇总] }  ──▶  明细表 + 汇总小表 + 导出
```

### 取数策略(方案A: kline 为主 + perf/outcome 冻结兜底)
> 修正(实施期核实后): perf 表是**夜间增量填充**, 对刚触发几天的近期信号只填到当前 day_offset
> (实测 6/1~6/5 买点只到 day_offset≈2)。若以 perf 为骨架算"区间最大浮盈/浮亏", 近期窗口会只
> 算两天而失真——而近期复盘正是主用例。故主算源改为 `cfzy_sys_kline_cache`(完整含最新收盘,
> 已用临时脚本验证), perf/outcome 冻结字段仅作"个股移出池、kline 停更"时的兜底。仍属方案A混合读取。

- **主算源 = kline_cache**: `cfzy_sys_kline_cache` 取该 code 在 `trigger_date` 当日及之后的全部 OHLC。
  - 当前收益 = `(最新close - 触发价)/触发价`,最新close = 该 code `MAX(trade_date)` 的 close。
  - 区间最大浮盈 = 触发后区间内 `MAX(high)` 相对触发价的涨幅。
  - 区间最大浮亏 = 触发后区间内 `MIN(low)` 相对触发价的跌幅。
  - T+1/T+3/T+5 = 触发后第 1/3/5 个**交易日**的 close 相对触发价收益(数 kline 行, 不足则 null)。
- **兜底 = perf/outcome 冻结字段**(当 kline 缺该 code 或行数不足, 即个股移出池停更):
  - perf 表 `cfzy_biz_signal_perf(signal_pk, day_offset 1..30, high_pct/low_pct/close_pct 相对触发价)`:
    区间最大浮盈=`MAX(high_pct)`、最大浮亏=`MIN(low_pct)`、T+N=`close_pct@day_offset=N`。
  - perf 也缺则回退信号行自带的 `outcome_p1/p3/p5_pct` 字段。
  - 走兜底的行在返回里置 `frozen=true`, 前端可标注"冻结值"。
- **评估结论**: 直接读信号行 `outcome` 字段(success/fail/neutral, 未满评估窗为 pending/null)。
- **板块/大盘信号无个股收益**: `SECTOR_*`/`PLUNGE_*` 不在 perf 快照范围(快照只选 6 位数字代码个股),
  且板块/大盘非个股标的, 这些行所有收益列留空(显示 "—"), 仅展示信号本身。

## 4. 类别 → 过滤规则映射

> 已核实生产库 `cfzy_biz_signals.direction` 真实取值集合 = **buy / sell / reduce / plunge**。
> 实测归属: 卖点=direction `sell`(SELL_BREAK_MA5/10/20、SELL_LOSS_10); 减仓=direction `reduce`
> (SELL_LOSS_5、SELL_LOSS_8、SELL_TAKE_PROFIT); 大盘=direction `plunge`(PLUNGE_*)。
> 关键: 板块 `SECTOR_*` 的 direction 也是 `buy`, 故"买点"必须叠加 signal_id 前缀才能与板块分开。

| 类别(前端 key) | 后端过滤条件(WHERE 片段) |
|---|---|
| `buy` 买点 | `direction='buy' AND signal_id LIKE 'BUY\_%'` |
| `sell` 卖点 | `direction='sell'` |
| `reduce` 减仓 | `direction='reduce'` |
| `sector` 板块预警 | `signal_id LIKE 'SECTOR\_%'` |
| `plunge` 大盘风控 | `direction='plunge'` |

勾选多个类别时, 各片段以 OR 拼接, 整体再 AND 上 `user_id` 与 `trigger_date BETWEEN start AND end`。
默认勾选 `buy,sell,reduce`。LIKE 中 `_` 需转义为 `\_`(避免通配单字符)。

## 4b. 计划性出场解析(卖点列)

个股买点触发后**常无真实 SELL_ 信号**(SELL_ 卖点是独立扫描, 与该买点不绑定; 实测某窗口12只买点票
全历史 0 条卖点)。因此"卖点"取**买点 `detail` 内嵌的「交易计划」**, 由纯函数 `parse_exit_plan(plan, trigger_price)` 解析:

| 计划串样例 | 解析结果 |
|---|---|
| `+7%卖半/剩半破MA10×0.98/-6%止损/T+10时停` | tp_pct=7 tp_action=卖半 tp_price=触发×1.07; sl_pct=6 sl_price=触发×0.94; time_stop=10; other="剩半破MA10×0.98 / T+10时停" |
| `+15%减半/-7%止损` | tp_pct=15 tp_action=减半 tp_price=触发×1.15; sl_pct=7 sl_price=触发×0.93 |
| (无计划: 强势起点/弱势极限) | 全 None/"" |

正则: 止盈 `\+(\d+(?:\.\d+)?)%(卖半|减半|卖|减)`; 止损 `-(\d+(?:\.\d+)?)%止损`; 时停 `T\+(\d+)时停`; 剩半条件 `剩半([^/|]+)`。

**触及标注**(复用已算的区间最大浮盈/浮亏, 不另查):
- `tp_hit` = `tp_pct 非空 AND max_gain_pct >= tp_pct`(区间最高浮盈达到止盈比例)
- `sl_hit` = `sl_pct 非空 AND max_dd_pct <= -sl_pct`(区间最低浮亏到达止损比例)
- **只标是否触及, 不计算"按计划卖出的实现收益"**(用户明确: 不做仿真)。

前端 `tp_label`="+15% 减半" / `sl_label`="-7%" 为展示串; 板块/大盘行无计划, 全留空。

## 5. 后端接口契约

### 端点
`GET /api/signals/review-list` (挂在现有 signals router, 与 outcome-stats/outcome-compare 同源)

参数:
- `start` (str, YYYY-MM-DD, 必填): 区间起(含)
- `end` (str, YYYY-MM-DD, 必填): 区间止(含)
- `categories` (str, 逗号分隔, 默认 `buy,sell,reduce`): 取值 buy/sell/reduce/sector/plunge
- user_id 取当前用户: `Depends(get_current_user)` 的 `user["id"]`(沿用 outcome-stats 写法)

返回:
```jsonc
{
  "start": "2026-06-01", "end": "2026-06-05",
  "latest_kline_date": "2026-06-05",        // 当前收益基准日, 前端提示用
  "rows": [
    {
      "code": "605358", "name": "立昂微",
      "signal_id": "BUY_STRONG_START", "signal_name": "强势起点(右侧)",
      "direction": "buy",
      "trigger_date": "2026-06-03", "trigger_time": "09:31",
      "trigger_price": 59.22,
      "cur_price": 66.77, "cur_ret_pct": 12.75,
      "max_gain_pct": 15.45, "max_dd_pct": -7.28,
      "t1_pct": 3.1, "t3_pct": 8.0, "t5_pct": null,  // 不足N交易日为 null
      "frozen": false,                       // true=走perf/outcome兜底(个股移出池kline停更)
      "tp_label": "+15% 减半", "tp_price": 68.10, "tp_hit": true,   // 计划止盈(无则 null/"")
      "sl_label": "-7%", "sl_price": 55.07, "sl_hit": false,         // 计划止损
      "other_exit": "剩半破MA10×0.98 / T+10时停",                     // 其余出场条件文本
      "outcome": "success",                  // success/fail/neutral/pending
      "trade_plan": "+15%减半/-7%止损",       // 从 detail 提取
      "detail": "前5日弱势极限... | 交易计划: ..."
    }
  ],
  "summary": [
    {
      "signal_id": "BUY_VOL_BREAKOUT", "signal_name": "缩量突破(右侧)",
      "count": 8, "win_rate": 25.0,
      "avg_cur_ret": -1.42, "median_cur_ret": -2.09,
      "avg_max_gain": 5.22, "avg_max_dd": -7.20,
      "avg_t5": null, "success_rate": null   // T+5/success 可能样本不足为 null
    }
  ]
}
```

### repo 函数
`backend/models/repo/signals.py` 新增:
```
async def get_review_signal_list(user_id: int, start: str, end: str,
                                 categories: list[str]) -> dict
```
- 一次查询取区间+类别信号行(含 detail/indicators/outcome 字段)
- 批量取相关 signal_pk 的 perf 聚合(MAX high_pct/MIN low_pct/close_pct@1·3·5)
- 批量取相关 code 的 kline 最新收盘
- Python 层组装 rows + 计算 summary(按 signal_id 分组: 笔数/胜率/均值/中位/均最大浮盈浮亏/T+5均/success率)
- `trade_plan` 用正则从 detail 提取 `交易计划:` 段

### 路由归属
已核实: 无 review 专属 router; `/review` 页胜率数据走 `signals.py`(outcome-compare/outcome-stats)。
故新端点就近挂 `backend/routers/signals.py`, 无需新建 router, 无需改 main.py 注册。

## 6. 前端设计

### 组件
`frontend/src/views/ReviewView.vue` 顶部新增卡片(可抽成子组件 `IntervalReviewCard.vue`, 若 ReviewView 已偏大则抽组件, 实施期视文件体量定)。

### 筛选栏
```
预设 [近5日][近2周][近1月][近3月]   起[NDatePicker]~终[NDatePicker] [查询]
类别 [✓买点][✓卖点][✓减仓][ ]板块预警[ ]大盘风控           [导出xlsx]
```
- 预设按钮点一下即设起止日期并自动查询; 预设的"工作日"换算用自然日近似(近5日=今起回推7自然日等), 起止仍以实际交易日数据为准。
- 类别多选用 NCheckboxGroup; 默认 [买点,卖点,减仓]。
- 进入页面默认载入"近5日 + 默认类别"。

### 明细表(NDataTable)
列(从左到右):
代码 | 名称 | 信号类型(中文) | 方向 | 触发日 | 触发价 | 现价 | 当前收益% | 区间最大浮盈% | 区间最大浮亏% | T+1% | T+3% | T+5% | 评估结论 | **计划止盈(label+目标价+触及)** | **计划止损(label+价+触及)** | **时停/其他出场** | 形态详情

- 计划止盈/止损列: 展示 `tp_label`(如 +15% 减半) + 目标价 + 触及徽标(`tp_hit`=是→橙色高亮); 止损同理(`sl_hit`)。无计划(强势起点/弱势极限/板块/大盘)显示 "—"。

- 收益类列: **A股配色 正红负绿**(沿用项目 AlertOverviewView 渲染: v>=0 用 `var(--red)`, v<0 用
  `var(--green)`), 百分号格式带正负号。(注意: 与西方红跌绿涨相反, 临时导出脚本曾用反了)
- 评估结论: success=成功(红)/fail=失败(绿)/neutral=中性(灰)/pending 或 null=待评估(橙)。
- 形态详情列宽窄, 完整 detail 用 NTooltip 悬浮或 NEllipsis 展开。
- 板块/大盘行收益列显示 "—"。
- 默认按触发日倒序; 表头冻结, 支持横向滚动。

### 汇总小表
明细表下方一张紧凑表, 列: 信号类型 | 笔数 | 胜率 | 均当前收益 | 中位 | 均最大浮盈 | 均最大浮亏 | T+5均 | success率。末行"全部"合计。

### 导出
- 引入 `xlsx`(sheetjs) 到 frontend 依赖。
- "导出xlsx"按钮: 把当前 rows + summary 写成两个 sheet(个股明细 / 按类型汇总), 文件名 `区间复盘_{start}_{end}.xlsx`, 带表头底色/列宽/盈亏底色。
- 复用本次会话临时脚本 `_review_export_xlsx.py` 的列设计与配色作为蓝本。

### 路由
无需新增路由, 复用 `/review`。

## 7. 边界与非目标(YAGNI)

- **不做**实时盘中刷新; 当前收益基准是 kline 最新收盘(日级)。
- **不做**自定义持有期 T+N(N 固定 1/3/5); 区间最大浮盈/浮亏已覆盖路径峰谷。
- **不做**跨用户/多账户对比; 沿用单 user_id。
- **不做**信号参数回测/重模拟(那是回测页职责); 本功能只读真实已触发账本。
- **不做**后端物化新表; perf 表即现成物化层。

## 8. 测试要点

- repo 函数: 给定区间+类别, 返回行数与库内真实记录一致(用本次 6/1~6/5 数据对账: 17 个股买点)。
- 类别过滤: 各前缀映射正确; 默认类别不含板块/大盘。
- 收益计算: 当前收益/区间最大浮盈浮亏/T+5 与冻结 perf/kline 推算一致; kline 缺失时走兜底并标 frozen。
- 汇总: 胜率/均值/中位与明细可手工核对。
- 前端: 预设按钮联动日期、类别勾选、空区间(无信号)友好提示、导出 xlsx 打开正常。

## 9. 涉及文件(已核实路径)

- 后端:
  - `backend/services/review_metrics.py`(新建纯逻辑): category 过滤拼接 / trade_plan 提取 / `parse_exit_plan` 计划性出场解析 / kline 收益计算 / summary 聚合。
  - `backend/models/repo/signals.py`: 异步编排函数 `get_review_signal_list` + `_exit_cols` 触及标注。复用现有 `fetch_kline_cache_for_codes(codes, min_trade_date)` 与 `_fetchall`。
  - `backend/routers/signals.py`: 新增 `GET /api/signals/review-list` 端点(仿 outcome-stats 写法, `Depends(get_current_user)`)。无需改 main.py。
  - `backend/tests/`: 新增 `test_review_signal_list.py`(测纯函数, 不连库, 仿 test_signal_outcome.py 风格)。
- 前端:
  - `frontend/src/api/signals.ts`: 新增 `fetchReviewSignals(start, end, categories)` + `ReviewSignalRow`/`ReviewSummaryRow` 类型。
  - `frontend/src/components/review/IntervalReviewCard.vue`: 新建子组件(筛选栏 + NDataTable 明细 + 汇总小表 + 导出按钮)。NDatePicker `type="daterange"` 复用 HistoryView 的 `recentTradingRange(n)` 助手, 返回 `[startTs,endTs]` 需转 `YYYY-MM-DD`。
  - `frontend/src/views/ReviewView.vue`: 顶部 page-header 后挂入 `<IntervalReviewCard />`。
  - `frontend/src/utils/exportXlsx.ts`: 新建 xlsx 导出工具(用 sheetjs, 两 sheet)。
  - `frontend/package.json`: `npm install xlsx`。
- 变更日志: `frontend/src/data/changelog.ts` 顶部加版本记录(当前最新 v1.7.323, 新增 v1.7.324)。
