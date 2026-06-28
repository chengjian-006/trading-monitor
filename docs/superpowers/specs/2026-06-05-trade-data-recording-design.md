# 交易数据细化记录 设计文档（思路A）

- 日期：2026-06-05
- 状态：待用户确认 → 进入实现计划
- 目标版本：v1.7.x（实现时分配）

## 1. 背景与目标

系统当前侧重「监控/信号」，交易侧只有原始交割单（`cfzy_biz_trades`）和零散的虚拟跟踪（`cfzy_biz_rally_track`）。
为支撑**买点分析**与**收益回测**，需要把交易数据尽量细化、结构化地沉淀下来，形成可切片、可归因、可回放的样本。

确认的诉求（来自 brainstorming）：
1. 四个分析单元都要：交易回合、每笔成交环境快照、买点归因、决策/未成交记录。
2. 环境快照**用现有缓存事后重建**，不在导入时实时抓外部接口。
3. 真实账户（平安交割单）与虚拟跟踪**统一进同一套回合表**，用 `source` 区分。

本 spec 含两条并行工作流：
- **工作流一（交易记录）**：交易回合/腿/环境/决策四表 + 两个重建任务（§3–§8）。
- **工作流二（全市场样本池）**：全市场日线回填 + 全市场分时往后累积（§9），用户已确认「都要」。

非目标（本 spec 不含）：
- 前端可视化页面（本 spec 只定数据层与任务；前端单独排期）。

## 2. 已有相关表（复用，不重造）

| 表 | 角色 | 在本设计中的定位 |
|---|---|---|
| `cfzy_biz_trades` | 原始交割单（买卖单） | **只读导入层**，作为真实「回合腿」的来源，不被派生数据污染 |
| `cfzy_biz_signals` | 信号日志（含 +1~+30 日回填） | 买点归因的关联对象（`entry_signal_pk`） |
| `cfzy_biz_rally_track` | 回踩MA20 虚拟持仓 | 虚拟「回合」的来源之一 |
| `cfzy_biz_signal_executions` | 「是否执行某信号」反馈 | 与决策日志互补，可交叉引用 |
| `cfzy_sys_kline_cache` | 日线 OHLCV | MFE/MAE/回撤/技术环境重建的数据源 |
| `cfzy_sys_intraday_snapshot` | 当日分时归档 | 盘口位置（intraday_position）重建源 |
| `cfzy_sys_emotion_snapshot` / `_market_breadth` / `_theme_heat` | 情绪/广度/题材热度 | 大盘与题材环境重建源 |
| `holdings.py` | 运行时 FIFO 成本/`entry_date`/`entry_model` 匹配 | 回合构建器复用其 FIFO 逻辑，落库为持久化回合 |

## 3. 架构总览

```
[cfzy_biz_trades]  [cfzy_biz_rally_track / 信号模拟]
        \                    /
         v                  v
   ┌──────────────────────────────┐
   │  回合构建器 (round-builder)    │  FIFO 聚合 → 回合头+腿
   └──────────────────────────────┘
         |                         |
         v                         v
 [cfzy_biz_trade_rounds]    [cfzy_biz_round_legs]
                                   |
                                   v
                    ┌──────────────────────────┐
                    │ 环境重建器 (context-rebuild)│ 从缓存回填
                    └──────────────────────────┘
                                   |
                                   v
                       [cfzy_biz_trade_context]

 [用户手工录入 / 信号未执行]  ──►  [cfzy_biz_decisions] ──(回填)──► 假设性收益
```

两个后台任务：
- **回合构建器**：读交割单 + 虚拟跟踪，按 (user_id, code, source) 做 FIFO，把成交聚成「开→平」回合，写回合头与腿。增量幂等（可重算）。
- **环境重建器**：对尚未重建的腿，从 K线/情绪/题材/分时缓存回填环境字段；缓存缺失的字段留空并标记，后续可补跑。

## 4. 表设计

> 约定：金额 `DECIMAL`，百分比 `DOUBLE`（与现有 `cfzy_biz_signals` 口径一致），时间 `DATETIME`。前缀沿用 `cfzy_biz_`。

### 4.1 `cfzy_biz_trade_rounds`（交易回合头）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INT PK AI | |
| `user_id` | INT | |
| `code` / `name` | VARCHAR(10/50) | |
| `source` | VARCHAR(10) | `real`(交割单) / `virtual`(rally_track/信号模拟) |
| `source_ref` | VARCHAR(40) | 虚拟回合溯源（如 rally_track.id / signal_id），真实回合可空 |
| `status` | VARCHAR(10) | `open` / `closed` |
| `open_date` / `open_time` | DATE / VARCHAR(8) | 首笔买入 |
| `close_date` / `close_time` | DATE / VARCHAR(8) | 清仓时刻（持仓中为空） |
| `entry_price` | DECIMAL(10,3) | 加权买入均价 |
| `exit_price` | DECIMAL(10,3) | 加权卖出均价（未平为空） |
| `peak_qty` | INT | 峰值持仓股数 |
| `is_scaled_in` / `is_scaled_out` | TINYINT | 是否分批加/减仓 |
| `total_buy_amount` / `total_sell_amount` | DECIMAL(12,2) | |
| `total_fee` | DECIMAL(10,2) | 佣金+印花+过户合计 |
| `realized_pnl` | DECIMAL(12,2) | 已实现盈亏（净，含费） |
| `realized_pnl_pct` | DOUBLE | 相对买入成本的收益率 |
| `holding_days` | INT | 自然/交易日（口径见 §6） |
| `mfe_pct` / `mfe_date` | DOUBLE / DATE | 持有期最大浮盈及发生日 |
| `mae_pct` / `mae_date` | DOUBLE / DATE | 持有期最大浮亏及发生日 |
| `max_drawdown_pct` | DOUBLE | 自持有期峰值的最大回撤 |
| `entry_signal_pk` | INT NULL | FK→`cfzy_biz_signals.id`，买点归因 |
| `entry_signal_id` | VARCHAR(40) NULL | 冗余信号编码，便于按模型聚合 |
| `entry_model_name` | VARCHAR(50) NULL | 模型中文名 |
| `entry_deviation_pct` | DOUBLE NULL | 我的买价 vs 信号触发价偏离%（正=买贵了） |
| `exit_reason` | VARCHAR(40) NULL | 止盈/止损/破位/时间止损/手动/未平 |
| `created_at` / `updated_at` | DATETIME | |

- 去重/幂等键：`uk_round`(`user_id`,`code`,`source`,`open_date`,`open_time`) — 同一开仓时刻一只票一个回合。
- 索引：`idx_user_status`(`user_id`,`status`)，`idx_entry_signal`(`entry_signal_id`)，`idx_close_date`(`close_date`)。

### 4.2 `cfzy_biz_round_legs`（回合腿/明细）

回合内每一笔买/卖动作，真实/虚拟在此归一化。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INT PK AI | |
| `round_id` | INT | FK→`trade_rounds.id` |
| `leg_type` | VARCHAR(4) | `buy` / `sell` |
| `trade_date` / `trade_time` | DATE / VARCHAR(8) | |
| `price` | DECIMAL(10,3) | |
| `qty` | INT | |
| `amount` | DECIMAL(12,2) | |
| `fee` | DECIMAL(10,2) | 本腿费用 |
| `is_virtual` | TINYINT | 1=虚拟腿 |
| `trade_id` | INT NULL | 真实腿→`cfzy_biz_trades.id`；虚拟腿空 |
| `running_qty` | INT | 本腿成交后剩余持仓（做分批/金字塔分析） |

- 索引：`idx_round`(`round_id`)，`uk_leg_trade`(`trade_id`)（真实腿唯一，防重复并入）。

### 4.3 `cfzy_biz_trade_context`（每腿环境快照，缓存重建）

行情切片分析的核心表，每个腿一行，事后从缓存回填。

| 列 | 类型 | 来源 |
|---|---|---|
| `id` | INT PK AI | |
| `leg_id` | INT | FK→`round_legs.id`，`uk_leg`(`leg_id`) 唯一 |
| `code` / `trade_date` / `trade_time` | | 冗余便于查询 |
| **大盘** | | |
| `index_pct_sh` / `index_pct_cyb` | DOUBLE | `cfzy_sys_market_snapshot`/overview 当日指数涨幅 |
| `breadth_ma20_ratio` | DOUBLE | `cfzy_sys_market_breadth` |
| `emotion_phase` | VARCHAR(20) | `cfzy_sys_emotion_snapshot`（取最接近成交时刻的一条） |
| `limit_up_count` / `seal_rate` | INT / DOUBLE | 同上 |
| **题材** | | |
| `theme` | VARCHAR(50) | 个股 concepts 命中的当日最热题材 |
| `theme_limit_up_count` | INT | `cfzy_sys_theme_heat` |
| **个股技术** | | |
| `vs_ma5` / `vs_ma10` / `vs_ma20` / `vs_ma60` | DOUBLE | 成交日收盘相对均线%（K线缓存算） |
| `dist_to_ma10_pct` | DOUBLE | 成交价距 MA10 % |
| `vol_ratio` | DOUBLE | 量比 |
| `turnover` | DOUBLE | 换手率 |
| `limit_up_days` | INT | 连板数 |
| `free_cap` | DOUBLE | 流通市值 |
| **盘口位置** | | |
| `intraday_position_pct` | DOUBLE | 成交价在当日 [low,high] 的相对位置（0=最低吸,100=最高追）；分时缓存有则进一步对 avg_price 定位 |
| **重建状态** | | |
| `is_reconstructed` | TINYINT | 是否已重建（部分字段缺失也置1，缺失字段留 NULL） |
| `missing_fields` | VARCHAR(255) NULL | 缓存缺失导致留空的字段名列表，便于补跑 |
| `reconstructed_at` | DATETIME | |

- 设计取舍：大盘字段对同一交易日多腿是冗余的，但**为分析查询便利刻意反范式**（一条 SQL 即可按行情切片），且重建是事后批处理、成本可接受。

### 4.4 `cfzy_biz_decisions`（决策/未成交日志）

记录「没发生的交易」用于复盘，并复用信号回填机制做假设性收益。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INT PK AI | |
| `user_id` / `code` / `name` | | |
| `decision_type` | VARCHAR(20) | `considered_buy`(看过没买)/`skipped_buy`(放弃)/`early_sell`(卖飞)/`hold_wrong`(该卖没卖)/`other` |
| `decision_date` | DATE | |
| `ref_price` | DECIMAL(10,3) NULL | 决策时参考价（用于算假设性收益的基准） |
| `related_signal_pk` | INT NULL | FK→`cfzy_biz_signals.id` |
| `related_round_id` | INT NULL | FK→`trade_rounds.id`（如卖飞关联某回合） |
| `reason` | TEXT NULL | 决策理由 |
| `hypo_p1_pct` / `hypo_p3_pct` / `hypo_p5_pct` | DOUBLE NULL | 若当时行动 N 日后收益（回填，复用信号 outcome 机制） |
| `hypo_evaluated_at` | DATETIME NULL | |
| `created_at` | DATETIME | |

- 索引：`idx_user_date`(`user_id`,`decision_date`)，`idx_type`(`decision_type`)。

## 5. 回合构建器（round-builder）

- 输入：`cfzy_biz_trades`（real）+ `cfzy_biz_rally_track` 及信号模拟（virtual）。
- 逻辑：按 (`user_id`,`code`,`source`) 时间排序做 **FIFO**——复用/抽取 `holdings.py` 现有 FIFO，落库为回合头+腿；持仓归零即闭合一个回合，新买入开启下一个。
- 归因：闭合或开仓时，按 (`code`,`open_date`) 就近匹配当日 `cfzy_biz_signals` 的 buy 信号，写 `entry_signal_*` 与 `entry_deviation_pct`（沿用 holdings 的 `entry_model` 匹配口径）。
- 幂等：以 `uk_round` + `uk_leg_trade` 保证可重复全量重算不产生重复；重算采用「按 user/code 重建该票全部回合」策略，避免半截状态。
- 触发：交割单导入后增量触发 + 每日收盘后批量（注册进 `cfzy_sys_scheduled_tasks`）。

## 6. 环境重建器（context-rebuild）口径

- 选取「最接近成交时刻」的快照：情绪/广度按 `captured_at ≤ trade_time` 的最后一条（参考 `get_last_emotion_before`）。
- 技术指标：从 `cfzy_sys_kline_cache` 取成交日及之前 N 日算 MA/量比/相对均线；缺数据则该字段 NULL 并记入 `missing_fields`。
- `holding_days`：按 K线缓存的交易日计数（非自然日），无缓存退化为自然日并标记。
- MFE/MAE/回撤：对回合 [open_date, close_date]（持仓中取至最新交易日）逐日取 high/low 相对 `entry_price` 求极值。
- 盘口位置：优先用 `cfzy_sys_intraday_snapshot` 对 `avg_price` 定位；无分时则退化为 [low,high] 线性位置。
- 触发：回合/腿写入后增量 + 每日批量补跑 `is_reconstructed=0` 的腿。

## 7. 与现有 FIFO（holdings.py）的衔接

- 现状：`holdings.py` 运行时算 FIFO 成本、`entry_date`、`entry_model`，**不落库**。
- 本设计：把该 FIFO 逻辑抽为可复用函数，回合构建器调用它产出持久化回合；前端持仓视图后续可改读回合表，避免两套口径漂移。
- 迁移期：保留 holdings 运行时计算作为校验基准，回合表数据与之对账一致后再切换前端读取。

## 8. 分期落地

**工作流一（交易记录）**
- **一期**：`cfzy_biz_trade_rounds` + `cfzy_biz_round_legs` + 回合构建器（real 优先，virtual 紧随）。先把「回合 + 买点归因」跑通。
- **二期**：`cfzy_biz_trade_context` + 环境重建器（依赖一期回合）。
- **三期**：`cfzy_biz_decisions` + 假设性收益回填（独立，可与二期并行）。

**工作流二（全市场样本池，可与工作流一并行）**
- **A 期**：全市场日线一次性历史回填 + 每日追加任务（立刻放大回测样本）。
- **B 期**：全市场分时每日冻结任务（往后累积）+ 保留/分区策略。

- 前端可视化各期完成后单独排期。

## 9. 工作流二：全市场样本池（日线回填 + 分时累积）

目标：放大买点回测的样本广度（股票数 × 交易日数），并为日内入场分析积累分时。
用户已确认两项都要。复用现有两张表结构，不新建主表。

### 9.1 全市场日线回填 + 每日追加（`cfzy_sys_kline_cache`）

- 复用现有表（`code`,`trade_date`,`open/high/low/close/volume`），仅把 population 从「自选池」扩到「全市场」。
- **历史回填**：一次性回填**5 年**全市场日线（已定）；数据源用非东财（baostock / akshare 非东财通道），分批限速、断点续跑、`INSERT IGNORE` 幂等。
- **每日追加**：收盘后对全市场当日日线增量入库（新任务，注册进 `cfzy_sys_scheduled_tasks`）。
- 量级：~5400 行/天，多年回填约几百万行、数百 MB～数 GB，存储压力小。
- 收益：所有 `bt_*.py` 回测样本从池内（几十只）放大到全市场。

### 9.2 全市场分时往后累积（`cfzy_sys_intraday_snapshot`）

- 复用现有表（`code`,`trade_date`,`data` JSON 分时数组）。
- **硬约束（务必周知）**：免费源（新浪/腾讯）分时仅「当日」，**历史分时无法回填**（东财 prod IP 已封）。因此全市场分时**只能从启用日起往后累积**，不会立即拥有历史。
- **任务**：每交易日 15:10 后对全市场逐只冻结当日分时（扩展现有 `intraday_snapshot_freezer` 从池内到全市场），限速 ~1.5 req/s、一小时内完成，避免触发封禁（参考既往东财封禁教训，优先新浪/腾讯）。
- 量级：~5400 行/天 ≈ 135 万行/年、约 9–10 GB/年（JSON 按股每日一行）。需配套**保留策略/分区**（如按年分表或定期归档冷数据），实现期定。
- 范围（已定）：**先全市场试**——直接上全市场逐只冻结，遇限频/封禁按批跳过并记 `missing` 缺口、不阻塞日线流程；降级方案（指数成份+真实成交/信号触发票）保留为应急回退，不作首发。

### 9.3 与工作流一的关系

- 工作流一的环境重建（§6）对**真实成交的票**只需这些票的日线（多在自选池已缓存），不强依赖工作流二即可先跑通。
- 工作流二完成后，环境重建与全市场回测都能用上更广的样本；两条流可并行开发，无硬依赖。

## 10. 配套约定

- 建表走 `database.py` 的 `SCHEMA_STATEMENTS`/`MIGRATION_STATEMENTS`，repo 层新增 `trade_rounds.py` / `decisions.py`（沿用 `_db.py` 异步封装）。
- 调度任务注册进 `cfzy_sys_scheduled_tasks` 种子。
- 每期改动在 `frontend/src/data/changelog.ts` 顶部加版本记录（项目规范）。
- 术语沿用 `docs/TERMS.md`（回合/买点/信号/快照）。

## 11. 决策记录 / 剩余开放问题

已定（2026-06-05）：
- 节奏：**按 §8 分期**。
- 全市场日线回填：**5 年**。
- 全市场分时：**先全市场试**，降级方案仅作应急回退。

剩余开放（可在实现计划阶段定，不阻塞）：
1. 虚拟回合的来源：一期是否纳入 `rally_track`，还是只先做 real、virtual 二期再并？（默认：一期 real，virtual 紧随）
2. `decisions` 录入方式：纯手工 API，还是也从「信号未执行（executions=skipped）」自动派生？（默认：先手工，自动派生后续加）
3. 全市场分时保留策略：长期全留 / 按年分表归档 / 只留近 N 年？（默认：先全留，量逼近阈值再按年分表）
