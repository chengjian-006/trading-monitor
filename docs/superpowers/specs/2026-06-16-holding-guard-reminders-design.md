# 持仓守护提醒 设计文档 (2026-06-16)

## 背景与目标

为持仓票增加两条**盯盘守护提醒**(只提醒、不自动下单),帮助用户在两个关键位置及时收到飞书/企微推送:

1. **接近前高**:持仓票现价逼近近期波段阻力,留意突破或受压。
2. **盈利保护**:曾大幅获利(峰值浮盈≥+10%)的票回吐到逼近成本线(≤+2%)时提醒,避免"把赚过钱的交易做成亏损离场"。

两条规则同属"持仓守护"族,数据来源/推送链路/节流机制高度重合,**合并为单一服务** `backend/services/holding_guard.py`,降低重复代码。

## 回测依据(盈利保护)

`backend/scripts/bt_profit_protect.py`(本地不入库,全市场5模型入场,bt_cache截至~2026-05-31)验证结论:

- 默认档(峰值≥+10% / 回吐≤+2% / T+20):全样本期望差 **Δ=+0.20%/笔**,胜率 **45%→57%**。
- 决定性数据:赚过+10%又回吐到≤+2%的赢家,**57% 继续拿最终做成亏损**(中位−1.24%),仅34%涨回>+2%。
- **model-dependent**:回踩MA20/强势起点/弱势极限/回踩MA10 上保本均增厚期望;**缩量突破/放量突破是例外**(全样Δ−1.12%,动量回踩多为健康洗盘再加速,机械保本砍在洗盘底)。
- 因是**提醒非自动卖**,例外的代价被软化(用户自行判断)。

详见记忆 `project_profit-protect-backtest`。

## 落点与形态

- 新建 `backend/services/holding_guard.py`,handler `holding_guard_tick()`。
- 注册:`task_registry.py` 加 handler + `database.py` 默认任务表(interval 60s)。
- 窗口门:交易日 09:25–15:00(仿 `rally_reminder_tick`)。
- 复用基建:
  - 持仓来源:`repository.get_holdings_full_info(1)` → cost/entry_date/entry_model 映射(`status=="hold"`)。
  - 现价:`data_fetcher.get_realtime_quotes([codes])`(一次批量)。
  - 日K:`data_fetcher.get_daily_kline(code, days=N)`(每票一次,两规则共用)。`N = max(70, 建仓至今自然日 + 10)` —— 规则A只需近65根,但规则B峰值须覆盖整个持有期(持仓超70天时固定70会漏掉更早的峰),故按建仓日动态放大取数天数。
  - 推送:`notifier.send_dual(...)`(企微+飞书双推)。
- 范围:**真实持仓**。模拟盘/自选暂不纳入(YAGNI,后续可加)。

## 规则 A:接近前高

- **前高**:`get_daily_kline(days=70)` 的日K,取**最近 65 根里跳过最近 5 根**、在更早 **60 根**窗口内的 `high` 最大值,记录其日期。
- **触发**:`ph×0.98 ≤ 现价 ≤ ph`(在前高下方、距离 ≤2%)。`现价 > ph`(已突破站上)不报(用户要的是"接近"非"突破")。
- K线不足(< 65 根,新股)→ 跳过。
- 文案示例:
  ```
  📈 平安银行(000001) 接近前高
  现价 ¥11.85,距近60日波段高 ¥12.05(05-21)仅 -1.7%
  留意:放量站上=突破确认,滞涨/长上影=阻力压制
  ```

## 规则 B:盈利保护

- **成本**:`get_holdings_full_info` 的 entry_cost;无成本(非持仓/缺数据)→ 跳过。
- **峰值浮盈**:从 entry_date 起、到今天的日K最高价 `peak_high`;`峰值浮盈 = peak_high/cost − 1`。
  - 实现:`get_daily_kline` 后取 `date ≥ entry_date` 的子段算 `high.max()`,与盘中现价取大(`peak = max(peak_high_daily, 现价)`)。
- **触发**:`峰值浮盈 ≥ +10%`(确实赚过)**且** `当前浮盈 = 现价/cost − 1 ≤ +2%`(回吐到逼近成本线)。
- **模型上下文**(回测发现的智能提示):若 entry_model 已知:
  - 缩量突破/放量突破类(`BUY_VOL_BREAKOUT` 等动量突破)→ 文案附:"动量突破回踩常是洗盘,留意而非急走"。
  - 其他模型 → 标准锁利提示。
  - entry_model 未知 → 标准提示,不附模型句。
- 文案示例:
  ```
  🛡️ XX股份(00XXXX) 盈利保护
  最高赚过 +18.3%,现仅 +1.4%(成本 ¥XX.XX),逼近成本线
  别让这笔赚过的交易做成亏损,考虑保本/锁利离场
  (建仓:回踩MA20)
  ```

## 范围与节流

- **节流**:每股每规则每日最多 1 次。MVP 用进程内 `{date: {(code, rule)}}` 集合,当天推过即跳过。
  - 取舍:服务重启会清当日记录,极端下同票同规则当天可能重推一次。**默认接受**(提醒类容忍度高);若实测烦人再升级为落表持久化。
- **落库**:默认**不**落信号库(纯推送),避免污染买卖点胜率统计。

## 数据流(单 tick)

```
holding_guard_tick():
  门: is_workday() and 09:25 ≤ now ≤ 15:00
  holdings = get_holdings_full_info(1)        # {code: (cost, entry_date, model)}
  if 无持仓: return
  quotes = get_realtime_quotes(codes)         # 批量一次
  for code in holdings:
     price = quotes[code].price; 无价→跳过
     df = get_daily_kline(code, days=max(70, 建仓至今自然日+10))   # 一次, 两规则共用; 覆盖持有期
     # 规则A 接近前高
     ph, ph_date = prior_high(df, win=60, skip=5)
     if ph and ph*0.98 ≤ price ≤ ph and not throttled(code,"prior_high"):
         send_dual(A文案); mark(code,"prior_high")
     # 规则B 盈利保护
     cost, entry_date, model = holdings[code]
     if cost and entry_date:
         peak = max(df[date≥entry_date].high.max(), price)
         if peak/cost-1 ≥ 0.10 and price/cost-1 ≤ 0.02 and not throttled(code,"profit_protect"):
             send_dual(B文案 + 模型上下文); mark(code,"profit_protect")
```

## 参数(集中常量,便于调)

| 参数 | 默认 | 说明 |
|---|---|---|
| `WINDOW_HIGH` | 60 | 前高回看窗口(日) |
| `SKIP_RECENT` | 5 | 前高跳过最近 N 根 |
| `NEAR_HIGH_TOL` | 0.02 | 接近前高阈值(≤2%) |
| `PEAK_GAIN_MIN` | 0.10 | 盈利保护:峰值浮盈门槛 |
| `GIVEBACK_MAX` | 0.02 | 盈利保护:回吐触发线(当前浮盈≤+2%) |
| `WIN_START/END` | 09:25/15:00 | 盘中窗口 |

## 测试

- 单元:`prior_high()`(跳最近5根/窗口不足/正好贴线边界)、盈利保护触发判定(峰值达标×回吐达标的真值表、缺成本/缺entry_date跳过)、节流(同日同规则只一次)、模型上下文分支。
- 用构造的 df/quotes mock,不依赖实时行情。

## 不做(YAGNI)

- 模拟盘/自选纳入;节流落表持久化;落信号库标点;突破站上(规则A的反向)。后续按需再加。

## 待用户确认的默认项(可改)

1. 推送渠道:**双推(企微+飞书)** ← 默认
2. 节流持久化:**内存版** ← 默认
3. 是否落库:**不落** ← 默认
