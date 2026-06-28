# 历史成交导入 设计文档 (2026-06-16)

## 背景与目标

现有「交易分析·导入交割单」支持两种入口(粘贴交割单 / 上传 Excel)。用户希望再增加一种 **历史成交** 导入,用平安证券「历史成交」页复制出的格式。两个使用场景都要支持:**当天收盘后导当天** 与 **补录过去某几天**。

历史成交格式(制表符分隔,带表头):

```
成交时间  证券代码  证券名称  操作      成交数量  成交均价  成交金额    合同编号    成交编号            委托时间
14:55:15  000725   京东方Ａ  证券卖出  756      6.090    4604.040   0212877912  0101000089154126  14:55:15
14:55:15  000725   京东方Ａ  证券卖出  1744     6.090    10620.960  0212877912  0101000089154124  14:55:15
14:15:50  000725   京东方Ａ  证券买入  2500     6.110    15275.000  0215408923  0101000076612694  14:15:50
```

与交割单的两点关键差异:
1. **无「成交日期」列** —— 只有 `成交时间`(HH:MM:SS)和 `委托时间`。
2. **按笔成交明细** —— 同一笔委托(同 `合同编号`)会拆成多行(如上 756 + 1744)。交割单则是按委托聚合的一行。

## 设计决策(已与用户确认)

- **日期来源**:前端日期选择器,默认今天,可改成过去任意一天;该日期注入本次粘贴的所有行。一个控件覆盖"当天 + 补历史"。
- **入库粒度**:按笔明细原样存(多行)。FIFO 汇总结果与按委托聚合完全一致,最忠实;不做聚合(YAGNI)。
- **防双算 = 替换该日**:导入某天时,**先删该用户该天的全部成交记录,再写入这批**。于是同一天交割单 vs 历史成交不再双算(该日以历史成交为准);重复导同一天 = 幂等。
- **残留约定(用户接受)**:交割单导入是纯追加(不按日替换)。故某天用历史成交补录后,**不要再用整份交割单覆盖那天**,否则交割单会按聚合口径再加一遍致双算。本期不改交割单导入逻辑(改动更大、收益有限)。

## 落点与形态

### 前端 `frontend/src/views/TradeAnalysisView.vue`

- 现有 `<NTabs>`(paste / upload)中间插入第三个 tab **「历史成交」**(name=`history`):
  - `<NDatePicker type="date">` 默认今天(`Date.now()` 初始化), 绑定 `historyDate`(时间戳)。
  - `<NInput type="textarea">` 绑定 `historyText`,placeholder「从平安证券历史成交复制内容后粘贴(无需日期列)」。
  - 「开始分析」按钮 → `handleHistoryAnalyze()`。
- `handleHistoryAnalyze`:校验非空 + 已选日期 → 调 `importHistory(text, dateStr)` → 复用现有结果渲染(summary/records 等)与错误提示。
- 移动端:date picker + textarea 自然竖排,沿用现有响应式,无额外适配。

### API 层 `frontend/src/api/trade-analysis.ts`

- 新增 `importHistory(text: string, tradeDate: string)` → `POST /api/trade-analysis/import-history`,body `{ text, trade_date }`(trade_date 形如 `2026-06-16`)。

### 路由 `backend/routers/trade_analysis.py`

- 新增 `POST /import-history`,入参 `ImportHistoryRequest { text: str, trade_date: str }`:
  - 解析 `trade_date`(`%Y-%m-%d`),非法 → `{ok: False, msg}`。
  - `trades = parse_history_text(text, trade_date)`;0 条 → 友好提示(连表头一起粘/支持历史成交格式)。
  - **先删后写**:`await repository.delete_trades_on_date(user_id, trade_date)`,再走现有 `_import_and_analyze(user_id, trades)`(其内部 `save_trade_records` 即 INSERT IGNORE,本批已无该日旧数据)。
  - 返回与 import-text 一致结构。

### 解析 `backend/services/trade_analyzer.py`

- 新增 `parse_history_text(text: str, trade_date: date) -> list[dict]`:
  - 复用 `_split_row` / `_build_colmap` 思路,但 **date 不再 required**:用一个不含 `date` 的必需集 `(code, op, quantity, price, amount)` 构造列映射;表头识别条件改为"含 `成交时间` 或 `成交编号`"(历史成交特征列)。
  - 每行复用 `_parse_row_mapped` 的字段提取 + **金额自洽护栏 `_amount_consistent`**(防列错位脏数据,与交割单同规则),但 `trade_date` 用传入参数注入(不从行里取)。
  - 操作列「证券买入/证券卖出」→ `buy`/`sell`;非这两值跳过。
  - 实现方式:抽出公共行解析,让交割单(date 来自行)与历史成交(date 来自参数)共用,避免复制。倾向把 `_parse_row_mapped` 加一个可选 `inject_date` 参数:传了就用它、不再解析行内日期。

### 仓储 `backend/models/repo/trades.py`

- 新增 `delete_trades_on_date(user_id: int, trade_date) -> int`:`DELETE FROM cfzy_biz_trades WHERE user_id=%s AND trade_date=%s`,返回删除行数(供日志/反馈)。

## 数据流(import-history 单次)

```
POST /import-history { text, trade_date }
  d = parse "%Y-%m-%d"(trade_date)           # 非法→提示
  trades = parse_history_text(text, d)        # 注入日期 + 金额护栏; 0条→提示
  delete_trades_on_date(user_id, d)           # 替换该日: 先清
  _import_and_analyze(user_id, trades)        # save(INSERT IGNORE)→全量重算→同步持仓→排程回合重建
  return { ok, record_count, ... }
```

## 测试(TDD,纯函数优先)

`backend/tests/test_history_trade_import.py`:
- 正常行解析:京东方三行,数量/价/金额正确,方向 buy/sell 正确。
- **日期注入**:解析结果 `trade_date` == 传入日期(行内无日期列)。
- **金额护栏**:金额≠价×量的脏行被拒(沿用交割单护栏)。
- 同一委托多笔(756 + 1744 同 14:55:15)都保留,不被误去重。
- 无表头 / 表头缺必需列 → 返回 [](不崩)。
- 操作列非买卖(如表头残留)→ 跳过。

`delete_trades_on_date` 为薄 SQL,纯函数解析覆盖核心逻辑;replace-by-date 的端到端正确性由"先删后写"次序保证,部署后实盘核对。

## 不做(YAGNI)

- 不新增 `成交编号`/`合同编号` 列入库(replace-by-date 已使重复导入幂等,无需精确去重键)。
- 不按委托聚合(按笔存,FIFO 结果一致)。
- 不改交割单导入为按日替换(残留约定已被用户接受)。
- 文件上传暂不支持历史成交(本期仅粘贴;Excel 走 import-excel 现有交割单路径)。

## 变更记录

按规范在 `frontend/src/data/changelog.ts` 头部加新版本(预计 v1.7.437)。
