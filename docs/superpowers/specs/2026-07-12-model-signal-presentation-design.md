# 模型/信号展示层三项增强 — 设计文档

日期: 2026-07-12
来源: 全项目审查后的「对标 TradingView / 果仁」借鉴项(见 open-threads 台账 0711 条)。
三项相互独立, 都落在模型/信号展示层, 共一份 spec, 实现顺序: 功能10 → 功能9 → 功能8。

---

## 功能10 — 图鉴补最大回撤 + 逐月胜率曲线

### 目标
模型图鉴当前只显示单个胜率数字, 掩盖回撤形态。仿果仁策略卡, 每个模型补: 逐月胜率迷你折线 + 最大回撤数值。

### 数据(后端)
夜间5分钟重算器 `model_winrate_refresher.refresh_model_winrate` 已按模型收集全部近6月交易(含触发日+扣费净收益)。扩 `_aggregate` 多算两样:

- **逐月胜率序列** `monthly`: 近6月每个自然月一项 `{ym, win_rate, n, net}`(该月触发交易的胜率/笔数/均收), 按 ym 升序, 空月不补。
- **最大回撤** `max_drawdown`: 把该模型近6月全部交易**按触发日升序**排成序列, 累计净收益曲线 `equity[i] = equity[i-1] + ret_i`(等权、每笔独立、不复利, 与现有胜率口径一致), 取 `max over i<j of (peak_before_j - equity_j)`, 即峰到谷最大回撤(百分点, 正数)。样本<5笔 → null(不显示)。

复用点: `_crunch_one` 已返回 `(model_name, date, ret)` 列表, `_aggregate` 已按模型分桶; 只需在分桶时保留 `(date, ret)` 对而非只留 ret, 再算月度与回撤。竞价弱转强/弱势极限同样参与(它们也在 acc 里)。

### 存储
`cfzy_biz_model_winrate` 加两列(迁移 ALTER, IF NOT EXISTS 幂等模式同现有):
- `monthly_json` TEXT NULL — JSON 数组 `[{ym,win_rate,n,net}, ...]`
- `max_drawdown` DOUBLE NULL — 百分点正数, null=样本不足

`repository.save_model_winrate` 的 upsert 语句加这两列。

### 接口
`GET /api/signals/model-winrate` 已存在, 返回体加 `monthly_json`(解析成数组)+`max_drawdown` 两字段。前端 `api/signals.ts` 的 `ModelWinrateItem` 类型补两字段。

### 前端(ModelsView.vue)
近3月胜率榜的每个模型行(或详情卡)加:
- **逐月胜率迷你折线**: inline SVG sparkline(不引 echarts, 自绘 polyline; 点=月胜率, hover 显示 ym+胜率+笔数), 空数据显 "—"。
- **最大回撤**: 一个数值 chip(如 "最大回撤 -18.2%"), null 显 "—"。
移动端: sparkline 用 max-width:100% 自适应, 回撤 chip 随卡片竖排。

### 口径同步(model-roster-sync)
本项只加展示维度, 不改任何模型规则/名册, 胜率数值仍自动重算。图鉴文案说明"回撤=逐笔权益曲线峰谷/月度=当月触发交易胜率"。changelog 记一条。

---

## 功能9 — snooze 到期语义升级

### 目标
推送卡静音从固定 N 天升级为三档语义: 仅今日 / 本周 / 直到再次突破。前两档日期型, 第三档条件型(引擎配合)。

### 落地页(替代直接静音)
推送卡的静音快捷链接改指向一个**极简签名落地页**(复用 push_pref 签名机制), 页面给三个按钮:
- `仅今日` → kind=snooze, days=1(until=今日)
- `本周` → kind=snooze, days=至本周日的天数(until=本周日)
- `直到再次突破` → kind=`snooze_until_retrigger`(新), target=`code|signal_id`

按钮各带独立签名(48h exp, 复用现有 HMAC + exp 机制)。落地页纯静态 HTML(后端渲染), 点按钮 GET 签名链接落库。

### 新 kind: snooze_until_retrigger
- `VALID_KINDS` 加该值。
- 存活判定: 不用 until_date(条件型), 用 `revoked_at IS NULL`; 需要新增判定逻辑而非纯 SQL 日期。
- push_pref 记录设置日 `set_date`(用现有 created_at 或 until_date 存设置日)。

### 引擎配合(解除条件)
信号推送闸(`send_wechat_signal` / `decide()` 消费 push_pref 的地方)对某 code+model 触发时:
1. 查是否有活跃 `snooze_until_retrigger`(该 code+signal_id)。
2. 若有: 查 `cfzy_biz_signals` 看该 code+signal_id **在上一个交易日**是否也触发过。
   - 上一交易日**未**触发(≥1交易日安静, 这是真·新一轮突破) → **解除该 snooze(revoke)** + 放行本次推送。
   - 上一交易日**也**触发(连续) → 继续压住(不推)。
3. 无该 snooze → 走原有闸门逻辑。

"上一个交易日" 用现有交易日历(chinese-calendar / trading_calendar)算; 查询 = `SELECT 1 FROM cfzy_biz_signals WHERE code=? AND signal_id=? AND DATE(triggered_at)=? LIMIT 1`。

### 前端(设置管理面板)
现有推送快捷设置管理面板(cfzy_biz_push_pref 展示)加 `snooze_until_retrigger` 类型的展示与手动撤销; kind_label 补中文"直到再次突破"。

### 测试
落地页三按钮生成正确签名链接; snooze_until_retrigger 解除条件(昨日有/无触发两分支); exp 过期拒绝(复用现有)。

---

## 功能8 — History 页加「按模型聚合」概览(纯前端)

### 目标
现有 /history 是可筛选的扁平信号列表。加一个模型维度的触发流概览, 不新建页/接口。

### 实现(HistoryView.vue, 纯前端)
- 页面顶部加**可折叠的「按模型聚合」概览区**(默认展开, 移动端默认折叠省空间)。
- 用已加载的 `allSignals`(当前日期范围内全部信号)按 `signal_id` 客户端分组, 每模型一张小卡:
  - 模型名 + 近N日触发数(N=当前筛选的日期范围)
  - 命中率: 复用已拉的 `fetchSignalOutcomeStats` 结果(按模型), 无则不显示
  - 最近3条触发快照: 代码+名称+触发价+理由首段(detail.split('|')[0])+相对时间
- 点模型卡 → 设 `filterModel = signal_id`(复用现有筛选), 主列表随之过滤; 再点取消。
- 卡片按近N日触发数降序; 买/卖用现有色阶区分方向。

### 无后端改动
所有数据来自 HistoryView 已有的 `fetchSignalHistory` + `fetchSignalOutcomeStats`。

### 测试
前端为主(组件渲染/分组逻辑); 后端无改动无新测试。

---

## 非目标(YAGNI)
- 不新建独立信号流水页(功能8 走 History 增强)。
- 不引 echarts 画 sparkline(自绘 SVG)。
- 不做复利/资金加权的回撤(等权逐笔, 与现有胜率口径一致)。
- snooze 不做"直到突破前高 X%"这类价格条件型(选的是"新一轮触发唤醒")。

## 验证闸(每项)
后端: pytest 全过 + import 干净。前端: vue-tsc + build 过。移动端: 三项都按 768 断点做卡片化/自适应(硬规范)。model-roster-sync: 功能10 图鉴四项清单相关的加 changelog + 文案。
