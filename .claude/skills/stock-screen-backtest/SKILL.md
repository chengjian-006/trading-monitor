---
name: stock-screen-backtest
description: 股小察项目专用——按现有买点模型选股, 并回测模型历史战绩(胜率/收益/PF), 全程5分钟真实可成交口径。当用户要"选股/筛股/今天哪些票触发了X模型/复盘某日信号", 或"回测某模型/看某买点的历史胜率收益/按月战绩"时使用。仅限本项目(需 cfzy_sys_kline_cache + cfzy_sys_kline_5m + baostock回填的5分钟数据)。
---

# 选股 + 历史战绩回测(5分钟真实可成交口径)

跑项目现有 6 个买点模型做**选股**和**历史战绩回测**。所有触发判定、可成交性、入场、出场全部走
**5分钟真实可成交口径**——不用日线全天量近似(那会高估可成交性, 尤其强势起点这类带实时累计额闸门的模型)。

## 何时用
- 选股: "今天/某日哪些票触发了回踩MA10", "自选股里现在有什么买点", "复盘 6-15 的信号"
- 回测: "回测强势起点近一年战绩", "缩量突破按月胜率", "全市场各模型 5分钟口径表现"

## 模型(6个, id → 名称)
BUY_RALLY_MA10 回踩MA10 / BUY_RALLY_MA20 回踩MA20 / BUY_VOL_BREAKOUT 缩量突破 /
BUY_PLATFORM_BREAKOUT 平台突破 / BUY_STRONG_START 强势起点 / BUY_WEAK_EXTREME 弱势极限

## 怎么用(本机直连生产库, 用 py -3)

**选股** — `.claude/skills/stock-screen-backtest/screen.py`
```
SEL_DATE=2026-06-18 SEL_UNIVERSE=pool:1 py -3 -u .claude/skills/stock-screen-backtest/screen.py
```
- `SEL_DATE` 选股交易日(默认库内最新交易日)
- `SEL_UNIVERSE` = `all` | `pool:1`(自选股, 默认) | `600519,000001`(指定)
- `SEL_MODELS` 只跑某几个(默认全部); `SEL_XLSX=1` 导出 xlsx
- 输出: 按模型分组的触发股票 + 入场价 + 理由 + 该模型近3月胜率

**回测** — `.claude/skills/stock-screen-backtest/backtest.py`
```
BT_UNIVERSE=pool:1 BT_MODELS=BUY_STRONG_START BT_MONTHLY=1 py -3 -u .claude/skills/stock-screen-backtest/backtest.py
```
- `BT_START`/`BT_END` 区间(默认近1年); `BT_UNIVERSE`(默认 `all`); `BT_MODELS`(默认全部); `BT_MONTHLY=1` 出按月表
- 输出: 各模型 n/胜率/均收/PF(+按月)

## 口径与边界(务必先告知用户)
1. **5分钟口径慢**: 全市场+全部模型回测约 1.5 小时。先用 `pool:1` 或单模型试, 大范围用后台跑。
2. **形态前提仍是日线**: 均线/主升浪/缩量/前高本就是日线指标(无5分钟均线主升浪); 5分钟只管触发/可成交/入场/出场。
3. **出场口径**: -X%止损 / +Y%卖半 按5分钟盘中触及即成交; 破均线按日收盘判定(均线是日线)。各模型用各自生产出场参数。
4. **覆盖**: 5分钟数据覆盖沪深A股约93.5%(5136只全年完整); **北交所不含**; 年内次新历史不足1年。
5. **幸存者偏差**: baostock清单只含当前在市票, 年内退市股不在内 → 胜率统计略偏高, 解读时提醒用户。
6. 数据源/回填见 backend/scripts/backfill_fullmarket_kline_5m.py(baostock后复权)。

## 注意
- 这是**离线/复盘**选股(基于历史库), 不是盘中实时——实时选股是生产 app 信号引擎的活, 别用本 skill 替代。
- 跑大范围回测前, 把口径边界(尤其幸存者偏差、5m慢)讲给用户, 别让结论被误读。
