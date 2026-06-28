# 术语表 (v1.7.x)

本项目用词易混 ("信号 / 预警 / 提醒 / 告警 / 通知"). 这里钉死规约, 之后新增功能严格对齐。

> **系统名 = 观潮**(原"股小察", 已更名; 历史 changelog 保留旧名不动)。用户界面/品牌一律"观潮"。
> **signal_id 编码**: 一律带方向/类别前缀 `BUY_/SELL_/SECTOR_/PLUNGE_/SCORE_`(v1.7.181 起), 下方示例已用现行 ID。
> **回踩买点中文名(全局唯一, 禁短名变体)**: `回踩20MA缩量后突破昨高` / `回踩10MA缩量后突破昨高`(= signal_engine emit 的 signal_name, 真相源); `缩量后放量突破`(BUY_VOL_BREAKOUT)。

## 1. 核心术语

| 用词 | 用在哪 | 严格含义 |
|---|---|---|
| **信号** (signal) | 代码层 / 内部技术名词 / `cfzy_biz_signals` 表 / `signal_id` | 模型按规则触发的一条记录, 是技术对象, 不带主观情绪色彩. 例: `BUY_STRONG_START` / `SELL_BREAK_MA5` |
| **预警** (alert) | 用户面 UI 文案 / 推送对外消息 | "用户需要看一眼"的信号子集, 强调"提醒注意". 例: "今日预警 (24只/33信号)", "盘中预警体系" |
| **推送** (push) | 通道层 — 企业微信 webhook | 把预警送出系统外的动作. 例: "企微推送", "推送已关闭" |
| **报告** (report) | AI 时段输出 — `cfzy_sys_market_reports` 表 | 09:26/10:00/11:30/14:00/15:00 自动生成的盘面综合分析, 由 LLM 写成. 不要叫"信号" |
| **快照** (snapshot) | 定时落库的市场状态 — `cfzy_sys_market_overview` 等 | 定点采集一份当时数据, 供前端读. 不参与触发判断 |

## 2. 用法对照

| ✗ 不要 | ✓ 应该 |
|---|---|
| "推送告警" / "告警消息" | **推送预警** / **预警消息** |
| "通知到企微" | **推送到企微** |
| "信号提醒" (用户视角) | **预警** |
| "提醒规则" | **信号规则** (技术) 或 **预警规则** (用户面文档) |
| "卖出告警" | **卖出预警** (用户面) / **卖出信号** (代码 direction='sell') |

## 3. 复合词

- **盘中预警**: 交易时段触发并实时推送的预警, 对应 priority=3 的信号
- **盘后汇总**: 15:05 cron 汇总一些 `alert_timing=post_close` 的信号一次性推送
- **持仓预警**: SELL_LOSS_5/8/10 / 追踪止盈 / 时间止损 等只对 hold 票生效的预警
- **大盘预警**: PLUNGE_INDEX / PLUNGE_BREADTH / PLUNGE_SPEED (direction='plunge')
- **预警体系**: AlertOverviewView 页面展示的所有信号规则汇总
- **预警矩阵**: 日期 × signal_id 的命中次数矩阵, AlertOverviewView 主体

## 4. 信号 6 大 group (`signal_specs.group_of`)

| group | 中文标签 | 涵盖 |
|---|---|---|
| `entry` | 买点 | BUY_WEAK_EXTREME / BUY_STRONG_START / BUY_RALLY_MA10 / BUY_RALLY_MA20 / BUY_VOL_BREAKOUT / BUY_PLATFORM_BREAKOUT / BUY_AUCTION_STRENGTH |
| `exit` | 卖点/减仓 | SELL_BREAK_MA5/10/20 / SELL_TAKE_PROFIT / SELL_TRAIL_STOP / SELL_RR_TARGET / SELL_TIME_STOP |
| `risk` | 持仓风控 | SELL_LOSS_5/8/10 |
| `regime` | 大盘+资金 | PLUNGE_INDEX / PLUNGE_BREADTH / PLUNGE_SPEED |
| `sector` | 板块 | SECTOR_CAPITAL_INFLOW |
| `quality` | 质量评分 | SCORE_STRENGTH / SCORE_THEME |

## 5. 写代码 / 写文案时怎么选

- **取信号 ID / 表字段 / API 路径 / 函数名**: 一律 `signal*` (英)
- **打日志 (内部)**: "Signal" / "信号"
- **企微推送文案 / UI 标题给用户看**: "预警"
- **跟用户对话 (issue / 文档说明)**: 优先 "预警" (用户视角); 解释技术细节时切到 "信号"
- **新加 view 名**: `XxxOverviewView` / `XxxConfigView` (英); 中文显示走 "...预警" / "...配置"
