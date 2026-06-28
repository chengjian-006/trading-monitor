---
name: model-roster-sync
description: Use when adding, removing, or changing any 买点/卖点模型 in this trading project — editing DEFAULT_SIGNAL_CONFIG params, detector logic, win-rate口径, or model rules in backend/services/signal_engine_config.py or signal_engine_detectors.py. Keeps the 模型图鉴 (models.ts / ModelsView.vue 流程图 / 模型推荐 / changelog) from drifting stale vs the actual model.
---

# 模型图鉴同步 (model roster sync)

改了模型的实际行为(参数/规则/胜率口径),图鉴若不同步就会**说一套、跑一套**。本项目已多次发生这种漂移
(竞价额 1亿→5000万 图鉴没改、缩量突破加了「下影线承接」图鉴没写、删了「10点门槛」caveat 还留着)。

**唯一真相源 = `backend/services/signal_engine_config.py` 的 `DEFAULT_SIGNAL_CONFIG` + `signal_engine_detectors.py` 的检测器逻辑。** 图鉴是它的镜像,必须跟着改。

## 改任何模型后,必同步这 5 个面(缺一即漂移)

| # | 面 | 文件 | 改什么 |
|---|---|---|---|
| 1 | 名册 | `frontend/src/data/models.ts` | 该模型的 `rules`(setup/trigger/gate/timing)、`exit`、`traits`、`caveats`、`scope` |
| 2 | 图鉴流程图 | `frontend/src/views/ModelsView.vue` | 硬编码的 `FLOW_STAGES[模型id]`(前置/触发/出场三段 items),**和 models.ts 完全一致** |
| 3 | 模型推荐 | `frontend/src/views/BacktestView.vue`(grep 模型名/结论定位) | 涉及该模型的推荐结论/口径文案 |
| 4 | 版本记录 | `frontend/src/data/changelog.ts` | 数组头部加一条版本记录(见 changelog 规约) |
| 5 | 参数中文名 | `frontend/src/data/paramLabels.ts`(+ `SignalConfigView.vue` 的 label) | **新增/改名任何 DEFAULT_SIGNAL_CONFIG 数值或开关参数,必须加一条中文 label**;否则模型回测页会退化成直接显示英文 key |

**系统页面所有面向用户的元素一律中文,不得直接暴露英文参数 key / 字段名 / 枚举值。**
模型回测页、信号配置页展示的临时参数走 `paramLabels.ts` 的 `paramLabel(key)`;漏登记会兜底显示原始英文 key(=暴露,算漂移)。
两份中文用词(`paramLabels.ts` 与 `SignalConfigView.vue`)必须一致,不得各说一套。

**胜率数值不用手改**:每日 17:30 自动重算写 `cfzy_biz_model_winrate`,页面实时拉。只有「胜率**口径**」变了(比如改了出场规则、回测窗口)才需在 1/2/3 的文案里同步。

## 定位法(别靠记行号)

用模型 id 一把抓出所有要改的地方:
```
grep -rn "BUY_XXX" frontend/src/data/models.ts frontend/src/views/ModelsView.vue frontend/src/views/BacktestView.vue
```
models.ts 的 `rules` 和 ModelsView 的 `FLOW_STAGES` 是**两份独立手写副本**,最容易只改一份 → 两边都要核。

## 改完必做的一致性核对(逐字段)

对该模型,拿 config 的实际值,逐条核 models.ts.rules 与 FLOW_STAGES:

- 数值类(门槛/倍数/容差/百分比):config 的 `min_full_day_amount`/`min_amount_now`/`vol_mult_*`/`breakout_pct`/`touch_pct`/`shrink_ratio` 等 ↔ 图鉴文案里的数字
- 开关类(`REQ_*`/`*_True`):config 里为 `True` 的前置条件,图鉴必须写;为 `False`(可选未启用)的,图鉴**别**列成生效条件
- 时间门槛:`intraday_earliest_minute`(0=不卡点 / 600=10:00 / 880=14:40 …)↔ timing 文案;**删了时间门槛要连带删 caveat 里「被X点压着」之类旧话**
- 出场:各模型出场参数 ↔ exit 文案

## 红旗(出现即说明漏同步)

- 只改了 `models.ts` 没碰 `ModelsView.vue`(Stop hook 会拦,但别依赖它兜底)
- config 加/删了一个 `REQ_*` 或门槛,图鉴 setup/trigger 字数没变
- timing 写「不卡10点」但 caveat 还说「被10点门槛压着」← 自相矛盾
- config 数字变了(如 5000万),图鉴还是旧数字(如 1亿)
- 模型回测/信号配置页冒出英文参数名(如 `vol_mult_avg10`)← `paramLabels.ts` 漏登记中文

## 注意

- 这是项目专用约定,真相源在后端 config,不要凭图鉴反推模型。
- 同步是「让镜像追上真相」,不是改模型逻辑;若同步时发现 config 本身可疑,先问用户,别擅自改 config(见生产配置改动确认规约)。
