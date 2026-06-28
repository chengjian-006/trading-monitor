# 模拟账户(纸面交易)功能设计

日期: 2026-06-09
状态: 待评审

## 1. 目标与背景

在系统内建一个**模拟账户**：自选池里只要触发个股买点就自动模拟买入、触发卖点就自动模拟卖出，严格按模型执行。用来回答一个核心问题——

> **严格按模型买卖点执行，是否能产生持续的正反馈(净收益为正)？**

它与现有"信号 outcome 胜率"(理论上每个信号点后 T+1/3/5 的涨跌)形成互补：信号 outcome 看的是**单点方向对错**，模拟账户看的是**带仓位管理+真实费用+买卖配对后的组合净值**。

## 2. 范围

**做(in scope):**
- 单一模拟账户(user_id=1), 初始资金用户可设(默认 100 万), 可一键重置。
- 实时执行: 个股买卖点信号触发即按触发价模拟成交, 含 A 股真实费用。
- 等额轮动仓位管理, 最多同时持 N 只(默认 10, 可设)。
- 账户概览 / 持仓 / 成交流水 / 资金曲线 / 按买点模型分组胜率 的前端页面与 API。
- 每日收盘盯市快照(画资金曲线)。

**不做(out of scope, 留待后续):**
- 多账户 / 多策略对比矩阵。
- 加仓 / 金字塔 / 动态仓位。
- 盘中分时撮合(只用信号触发价, 不模拟盘口/滑点队列)。
- 自定义费率以外的交易规则(如 T+0、融券)。

## 3. 账户规则(已与用户确认)

| 项 | 规则 |
|---|---|
| 初始资金 | **用户可设**, 默认 1,000,000 元 |
| 仓位管理 | 等额轮动: 每个买点目标金额 = 当前总资产(成本口径) / 最大持仓数; 最多同时持 N 只(默认 10, 可设); 资金不足该买点则跳过并记日志。总资产(成本口径) = 现金 + Σ持仓成本(cost_amount), 不依赖盘中实时市值, 保证成交确定性可复现 |
| 成交范围 | 自选池触发的全部个股买卖点(`direction` ∈ buy/sell/reduce), 排除板块/大盘级(signal_group ∈ regime/sector)与非 6 位代码 |
| 成交时机 | **实时**: 信号确认触发(即会推送)的同一时刻成交 |
| 成交价 | 信号触发价(`cfzy_biz_signals.price`) |
| 费用 | 买 = 佣金(万2.5, 最低5元) + 过户(万0.1); 卖 = 佣金(万2.5, 最低5元) + 印花税(千1) + 过户(万0.1) |
| 买点命中已持仓 | 忽略, 不加仓 |
| 卖点语义 | `SELL_*_HALF` 与 `reduce` 类 → 卖一半(向下取整到100股, 不足整手则全清); 其他卖点 → 全部清仓; 命中未持仓 → 忽略 |
| 股数取整 | 买入按 100 股整手向下取整, 不足 100 股则跳过 |

费率默认值存账户配置, 可后续调整。

## 4. 数据模型(独立 paper schema, 不污染实盘回合表)

新增 4 张表, 均 `cfzy_biz_paper_*` 前缀, 含 `user_id` 隔离。

### 4.1 cfzy_biz_paper_account (账户, 每用户一行)
- `id` PK, `user_id` UNIQUE NOT NULL DEFAULT 1
- `name` VARCHAR(64) DEFAULT '模拟账户'
- `initial_capital` DECIMAL(14,2) NOT NULL DEFAULT 1000000.00  — 用户可设
- `cash` DECIMAL(14,2) NOT NULL  — 当前可用现金
- `max_positions` INT NOT NULL DEFAULT 10  — 用户可设
- `commission_rate` DECIMAL(7,6) DEFAULT 0.000250, `min_commission` DECIMAL(6,2) DEFAULT 5.00
- `stamp_rate` DECIMAL(7,6) DEFAULT 0.001000, `transfer_rate` DECIMAL(7,6) DEFAULT 0.000010
- `started_at` DATETIME, `reset_at` DATETIME NULL
- `created_at`, `updated_at`

### 4.2 cfzy_biz_paper_position (当前持仓)
- `id` PK, `account_id` FK, `user_id`
- `code` VARCHAR(8), `name` VARCHAR(32)
- `qty` INT  — 持股数
- `cost_amount` DECIMAL(14,2)  — 累计买入成本(含买入费), avg_cost = cost_amount/qty
- `open_date` DATE, `open_time` DATETIME
- `entry_signal_pk` INT NULL, `entry_signal_id` VARCHAR(48), `entry_model_name` VARCHAR(48)
- UNIQUE(`account_id`, `code`)  — 一只票最多一个未平仓位

### 4.3 cfzy_biz_paper_trade (成交流水 = 每一笔买/卖)
- `id` PK, `account_id` FK, `user_id`
- `code`, `name`
- `side` ENUM('buy','sell')
- `qty` INT, `price` DECIMAL(10,3), `amount` DECIMAL(14,2), `fee` DECIMAL(10,2)
- `cash_after` DECIMAL(14,2)  — 成交后现金, 便于审计
- `signal_pk` INT NULL, `signal_id` VARCHAR(48), `signal_name` VARCHAR(64), `signal_direction` VARCHAR(12)
- `realized_pnl` DECIMAL(14,2) NULL, `realized_pnl_pct` DECIMAL(8,3) NULL  — 仅卖出时填(净收益 vs 卖出对应成本)
- `note` VARCHAR(64) NULL  — 如 '卖半'/'清仓'
- `trade_date` DATE, `trade_time` DATETIME
- INDEX(`account_id`, `trade_date`), INDEX(`signal_pk`)

### 4.4 cfzy_biz_paper_equity (每日收盘市值快照, 资金曲线)
- `id` PK, `account_id` FK, `user_id`
- `snap_date` DATE, UNIQUE(`account_id`, `snap_date`)
- `cash` DECIMAL(14,2), `holdings_mv` DECIMAL(14,2), `total_equity` DECIMAL(14,2)
- `total_return_pct` DECIMAL(8,3)  — vs initial_capital
- `position_count` INT
- `created_at`

## 5. 组件与数据流

### 5.1 执行器 `backend/services/paper_trader.py`
入口 `async def on_signal(signal: dict)`, 在**信号确认触发(会推送买卖点)的同一处**调用; **仅生产环境执行一次**, 异常全部吞掉(绝不影响信号/推送主流程)。

决策逻辑拆成**纯函数** `decide(account, position, signal) -> Action`(便于单测), DB 读写为薄封装:
- 过滤: direction ∈ {buy,sell,reduce} 且 code 为 6 位数字 且 signal_group ∉ {regime,sector}。
- 幂等: 若该 `signal_pk` 已有 paper_trade 记录, 跳过(防重复执行)。
- **买**: 已持仓→忽略; 持仓数≥max→跳过(日志); target = 总资产/max; qty = 向下取整到整手的 min(target可买, 现金可买); qty<100→跳过; 扣现金、建仓、记流水。
- **卖/减**: 未持仓→忽略; HALF/reduce→卖半(整手, 不足整手全清), 其他→全清; 算净收益、回补现金、更新/平仓、记流水(realized_pnl)。

接入点: 待实现时定位 signal_engine 中"个股买卖点确认并推送"的唯一处, 在其后 `await paper_trader.on_signal(...)`。若该处难以单点接入, 退而用一个轻量"新信号消费"钩子(读 `cfzy_biz_signals` 当日新插入且未被模拟账户处理的个股买卖点)。

### 5.2 每日盯市任务 `paper_equity_snapshot`
注册到 `cfzy_sys_scheduled_tasks`, cron 每交易日 15:05。对每个未平仓位取当日收盘价(kline_cache / 实时), 算 holdings_mv 与 total_equity, UPSERT 当日 `cfzy_biz_paper_equity`。

### 5.3 API `backend/routers/paper_trading.py` (前缀 `/api/paper-trading`)
- `GET /summary`: 总资产(现金+持仓实时市值)、累计收益率、已实现盈亏、已实现胜率、盈亏比、最大回撤(由 equity 曲线算)、持仓数、运行天数。
- `GET /positions`: 当前持仓(含实时价与浮盈)。
- `GET /trades?limit&offset`: 成交流水(分页)。
- `GET /equity`: 资金曲线(paper_equity 序列)。
- `GET /model-stats`: 按 `entry_model_name` 分组的已实现胜率/平均盈亏/笔数 — 直接回答"哪些买点模型正反馈"。
- `POST /reset` (admin): 用给定 `initial_capital`/`max_positions` 清空并重置账户(清持仓/流水/曲线, 现金=本金)。
- `PUT /settings` (admin): 改 initial_capital(仅在重置时生效)/max_positions/费率。

### 5.4 前端 `frontend/src/views/PaperTradingView.vue` (挂"复盘"菜单组)
- 账户概览卡: 总资产、累计收益率、已实现胜率/盈亏比、最大回撤、持仓数。
- 资金曲线图(对照初始本金基准线)。
- 持仓表(代码/名称/股数/成本/现价/浮盈%/买点模型)。
- 成交流水表(时间/买卖/价/费/对应信号/已实现盈亏)。
- 按买点模型胜率表。
- 设置区(初始资金、最大持仓数)+ 重置按钮(NPopconfirm + 操作反馈)。
- store `frontend/src/stores/paper-trading.ts`, api `frontend/src/api/paper-trading.ts`。

## 6. 边界与异常

- **资金不足/无整手可买**: 跳过该买点, logger.info 记录(不建流水)。
- **触发价缺失或≤0**: 跳过, 不成交。
- **同一信号重复进入**: 靠 signals 表 `uk_signal_day` + 执行器 signal_pk 幂等双保险。
- **执行器任何异常**: try/except 吞掉并告警, 不影响信号落库与推送。
- **停牌/退市**: 盯市取不到价时, 持仓按最近一次有效收盘价估值, 标注。
- **非生产环境**: 执行器与盯市任务跳过(对齐现有 is_production 网关), 避免本地重复写库。

## 7. 验证产出(对应用户目的)

- 资金曲线 + 累计收益率: 直观看"按模型执行净值是否向上"。
- 已实现胜率 / 盈亏比 / 最大回撤: 组合层面的正反馈强度。
- 按买点模型分组的实现胜率/平均盈亏: 看**哪些模型贡献正反馈、哪些拖累**。
- 与现有信号 outcome 胜率并列, 形成"理论方向" vs "模拟成交"双视角。

## 8. 测试

- `backend/tests/test_paper_trader.py`: 对纯函数 `decide`/费用计算/整手取整/卖半/清仓/现金不足/已实现盈亏 做单测(不连库)。
- 手工: 本地用一组构造信号跑执行器(非生产网关需临时放开或注入 account state), 校验现金/持仓/流水/盈亏自洽。

## 9. 落地顺序(预告, 详见实现计划)

1. 建表(database.py SCHEMA) + repo(paper_account/position/trade/equity CRUD)。
2. 执行器纯函数 + 单测 → DB 薄封装 → 接入信号触发点。
3. 盯市任务 + 注册 scheduled_task。
4. API 路由。
5. 前端页面/store/api + 菜单路由。
6. changelog + 部署。

## 10. 已解决的决策(无遗留 TBD)

仓位=等额轮动固定本金(本金可设)、范围=全部个股买卖点、费用=A股真实费用按触发价、时机=实时、买点不加仓、HALF/reduce卖半其余清仓、单账户可重置。
