"""全市场各买入模型 按周回测 (v1.7.x, 路线B).

每周(周六开市闲时)拉全A日K, 跑5个买入模型近半年回测, 算 胜率/持有/资金加权占用(卖半)/
单笔净收益/扣资金成本/年化资金效率/盈利因子, 写 cfzy_biz_model_backtest, 供面板按周展示。

数据: 新浪直连(node=hs_a, 剔北交所/ST/退市; getKLineData scale=240). 内存安全: 逐票抓→检测→只留成交结果元组, 丢K线。
口径与 backend/scripts/bt_buypoint_halfyear.py 一致(各模型各自出场, 卖半资金加权占用, 扣费0.30%, 资金成本年化6%)。
竞价弱转强用日K模拟(昨收→今开=高开, 不需要历史竞价), 参与全市场回测。
"""
import asyncio
import json
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from backend import data_fetcher
from backend.models import repository
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.signal_engine_detectors import (
    _detect_rally_ma20_pullback, _detect_vol_breakout,
    _detect_s0_weak_extreme, _detect_strong_start_right,
    _detect_platform_breakout,
)
from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG

logger = logging.getLogger(__name__)

MIN_BARS = 65
HALF_YEAR_DAYS = 182          # 近半年入场窗口
DEDUP, FEE, ANNUAL, YEAR_TD = 10, 0.003, 0.06, 245
WCFG = DEFAULT_SIGNAL_CONFIG["BUY_WEAK_EXTREME"]
MODELS = [
    ("BUY_PLATFORM_BREAKOUT", "中继平台突破"),
    ("BUY_VOL_BREAKOUT", "缩量后放量突破"),
    ("BUY_RALLY_MA20", "回踩20MA缩量后突破昨高"),
    ("BUY_RALLY_MA10", "回踩10MA缩量后突破昨高"),
    ("BUY_RALLY_MA60", "回踩60MA缩量后突破昨高"),
    ("BUY_STRONG_START", "强势起点"),
    ("BUY_WEAK_EXTREME", "弱势极限"),
    ("BUY_AUCTION_STRENGTH", "竞价弱转强"),
]
_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def _sim_right(entry, o, h, c, m10, i, n, cap=10, hard=-0.06, target=0.07):
    last = i + cap
    if last > n - 1:
        return None
    half, rf, dh = False, 0.0, None
    for t in range(i + 1, last + 1):
        if not half:
            if c[t] <= entry * (1 + hard):
                return c[t] / entry - 1.0, t - i, t - i
            if h[t] >= entry * (1 + target):
                rf = (o[t] / entry - 1.0) if o[t] >= entry * (1 + target) else target
                half, dh = True, t - i
        if not np.isnan(m10[t]) and c[t] < m10[t] * 0.98:
            r = (0.5 * rf + 0.5 * (c[t] / entry - 1.0)) if half else (c[t] / entry - 1.0)
            sp = t - i
            return r, sp, (dh + sp) / 2 if half else sp
    cl = c[last]
    r = (0.5 * rf + 0.5 * (cl / entry - 1.0)) if half else (cl / entry - 1.0)
    return r, cap, (dh + cap) / 2 if half else cap


def _sim_left(entry, c, i, n, cap=15, hard=-0.12):
    last = i + cap
    if last > n - 1:
        return None
    for t in range(i + 1, last + 1):
        if c[t] <= entry * (1 + hard):
            return c[t] / entry - 1.0, t - i, t - i
    return c[last] / entry - 1.0, cap, cap


def _sim_rally_ma5(entry, o, h, c, m5, i, n, cap=10, hard=-0.06, target=0.07):
    """v1.7.584 回踩族(回踩MA10/MA20/缩量突破)真实出场 —— 匹配实盘 rally_reminder 框架:
    未卖半仅 -6%止损 / +7%转卖半 / T+10时停(不判破均线); 卖半后剩半 收盘破MA5 即清(沿5日线飘, 无×容差)。
    与 _sim_right 的差异: ①剩半跟踪 MA5 而非 MA10×0.98 ②未卖半段不判破均线(实盘就只-6%止损)。
    全市场双窗OOS: 回踩MA10/MA20/缩量突破 三模型独立样本 胜率/均收/PF 全升(回踩MA20提升最大)。"""
    last = i + cap
    if last > n - 1:
        return None
    half, rf, dh = False, 0.0, None
    for t in range(i + 1, last + 1):
        if not half:
            if c[t] <= entry * (1 + hard):
                return c[t] / entry - 1.0, t - i, t - i
            if h[t] >= entry * (1 + target):
                rf = (o[t] / entry - 1.0) if o[t] >= entry * (1 + target) else target
                half, dh = True, t - i
                continue                       # 转卖半当天不再判剩半破线(次日起判)
        elif not np.isnan(m5[t]) and c[t] < m5[t]:
            r = 0.5 * rf + 0.5 * (c[t] / entry - 1.0)
            sp = t - i
            return r, sp, (dh + sp) / 2
    cl = c[last]
    r = (0.5 * rf + 0.5 * (cl / entry - 1.0)) if half else (cl / entry - 1.0)
    return r, cap, (dh + cap) / 2 if half else cap


def _sim_rally20(entry, o, h, c, m20, i, n, cap=15, hard=-0.07, target=0.15):
    """[已弃用于胜率口径, v1.7.584 回踩MA20 改走 _sim_rally_ma5 对齐实盘] 旧: +15%卖半/-7%止损/剩半破20线×0.97/T+15。"""
    last = i + cap
    if last > n - 1:
        return None
    half, rf, dh = False, 0.0, None
    for t in range(i + 1, last + 1):
        if not half:
            if c[t] <= entry * (1 + hard):
                return c[t] / entry - 1.0, t - i, t - i
            if h[t] >= entry * (1 + target):
                rf = (o[t] / entry - 1.0) if o[t] >= entry * (1 + target) else target
                half, dh = True, t - i
        if not np.isnan(m20[t]) and c[t] < m20[t] * 0.97:
            r = (0.5 * rf + 0.5 * (c[t] / entry - 1.0)) if half else (c[t] / entry - 1.0)
            sp = t - i
            return r, sp, (dh + sp) / 2 if half else sp
    cl = c[last]
    r = (0.5 * rf + 0.5 * (cl / entry - 1.0)) if half else (cl / entry - 1.0)
    return r, cap, (dh + cap) / 2 if half else cap


async def _build_universe(client) -> list:
    """全A列表(剔北交所/ST/退市), 返回 [(code, sina_sym)]。"""
    out, page = [], 1
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodeData")
    while page <= 90:
        params = {"page": page, "num": 80, "sort": "symbol", "asc": 1, "node": "hs_a", "_s_r_a": "page"}
        try:
            r = await client.get(url, params=params, headers=_H, timeout=15)
            txt = r.text.strip()
        except Exception:
            await asyncio.sleep(1)
            continue
        if not txt or txt == "null":
            break
        try:
            rows = json.loads(txt)
        except Exception:
            break
        if not rows:
            break
        for it in rows:
            sym, name = it.get("symbol", ""), it.get("name", "")
            if not (sym.startswith("sh") or sym.startswith("sz")):
                continue
            if any(t in name for t in ("ST", "退")) or name.startswith("*"):
                continue
            out.append((sym[2:], sym))
        page += 1
    return out


async def _fetch_kl(client, sym):
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/data/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen=350")
    try:
        r = await client.get(url, headers=_H, timeout=12)
        txt = r.text
        s, e = txt.find("("), txt.rfind(")")
        if s < 0 or e <= s:
            return None
        data = json.loads(txt[s + 1:e])
        if not data or len(data) < MIN_BARS + 5:
            return None
        df = pd.DataFrame(data).rename(columns={"day": "date"})
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["date", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
    except Exception:
        return None


def _backtest_one(df, start_date, code=""):
    """对一只票跑各模型, 返回 {model_name: [(ret_after_fee, span, eff), ...]}。纯CPU。

    注意: 本周回测仍是 EOD 日线口径(用全天数据检测), 有已知的乐观前视; 推送/图鉴用的
    每日胜率(model_winrate_refresher)已切 5分钟诚实口径, 面板周报仅作趋势参考。"""
    res = {name: [] for _, name in MODELS}
    if df is None or len(df) < MIN_BARS + 5:
        return res
    ind = compute_indicators(df)
    ind["amount_est"] = ind["volume"] * ind["close"]
    vol_avg10 = ind["volume"].rolling(10, min_periods=5).mean().values
    o = ind["open"].values; h = ind["high"].values; c = ind["close"].values
    v = ind["volume"].values
    m5 = ind["ma5"].values
    m10 = ind["ma10"].values; m20 = ind["ma20"].values; m60 = ind["ma60"].values
    dts = ind["date"].values; ph = ind["high"].shift(1).values
    n = len(ind)
    last_dt = {name: None for _, name in MODELS}

    def keep(name, i):
        dt = pd.Timestamp(str(dts[i])[:10])
        if last_dt[name] is not None and (dt - last_dt[name]).days <= DEDUP:
            return False
        last_dt[name] = dt
        return True

    for i in range(MIN_BARS, n):
        if dts[i] < start_date:
            continue
        latest = ind.iloc[i]; sub = ind.iloc[:i + 1]
        # v1.7.593: 回踩MA10/MA20/缩量突破 出场统一 B5(_sim_rally_ma5, 剩半沿5日线飘) 对齐实盘 rally_reminder
        #   与每日胜率重算(model_winrate_refresher v1.7.584 已切), 此前周回测仍旧口径造成同模型两处两说。
        if _detect_vol_breakout(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_VOL_BREAKOUT"]) and keep("缩量后放量突破", i):
            trig = ph[i] * 1.02; entry = o[i] if o[i] >= trig else trig
            r = _sim_rally_ma5(entry, o, h, c, m5, i, n)
            if r: res["缩量后放量突破"].append((r[0] - FEE, r[1], r[2]))
        if _detect_rally_ma20_pullback(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA20"]) and keep("回踩20MA缩量后突破昨高", i):
            trig = ph[i] * 1.025; entry = o[i] if o[i] >= trig else trig
            r = _sim_rally_ma5(entry, o, h, c, m5, i, n)
            if r: res["回踩20MA缩量后突破昨高"].append((r[0] - FEE, r[1], r[2]))
        if _detect_rally_ma20_pullback(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA10"]) and keep("回踩10MA缩量后突破昨高", i):
            trig = ph[i] * 1.025; entry = o[i] if o[i] >= trig else trig
            r = _sim_rally_ma5(entry, o, h, c, m5, i, n)
            if r: res["回踩10MA缩量后突破昨高"].append((r[0] - FEE, r[1], r[2]))
        # v1.7.593 回踩MA60(中线六二法60日档): 同检测器锚MA60, 出场B5(OOS: 破MA5 PF1.96 > 破MA10 1.39 > 破MA20 1.19)
        if _detect_rally_ma20_pullback(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA60"]) and keep("回踩60MA缩量后突破昨高", i):
            trig = ph[i] * 1.025; entry = o[i] if o[i] >= trig else trig
            r = _sim_rally_ma5(entry, o, h, c, m5, i, n)
            if r: res["回踩60MA缩量后突破昨高"].append((r[0] - FEE, r[1], r[2]))
        if not np.isnan(m10[i]) and _detect_strong_start_right(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_STRONG_START"], WCFG) and keep("强势起点", i):
            r = _sim_right(c[i], o, h, c, m10, i, n)
            if r: res["强势起点"].append((r[0] - FEE, r[1], r[2]))
        if _detect_s0_weak_extreme(sub, latest, WCFG) and keep("弱势极限", i):
            r = _sim_left(c[i], c, i, n)
            if r: res["弱势极限"].append((r[0] - FEE, r[1], r[2]))
        # 中继平台突破: 收盘确认(尾盘14:40判, 现价≈收盘) → 入场=触发日收盘; 出场同回踩MA10族
        if _detect_platform_breakout(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_PLATFORM_BREAKOUT"]) and keep("中继平台突破", i):
            r = _sim_right(c[i], o, h, c, m10, i, n)
            if r: res["中继平台突破"].append((r[0] - FEE, r[1], r[2]))
        # 竞价弱转强: T-1 强势缩量小回调 + T 高开≥3%, 入场=T开盘价, 出场同右侧族
        # v1.7.598: keep() 从条件判断前移到真触发时 —— 旧写法每天都消耗去重窗口(不管是否触发),
        # 等于每11天只有1天有资格被检测, 样本被随机欠采样。
        if i >= MIN_BARS:
            prev = ind.iloc[i - 1]; pc = float(prev["close"])
            pv = float(prev["volume"]); po = float(prev["open"])
            pm10 = float(prev.get("ma10", 0) or 0); pm20 = float(prev.get("ma20", 0) or 0)
            pm60 = float(prev.get("ma60", 0) or 0)
            pavg10 = float(vol_avg10[i - 1]) if i > 0 and not np.isnan(vol_avg10[i - 1]) else 0
            p20c = ind["close"].iloc[i - 21] if i >= 21 else pc
            if pc > 0 and pm20 > 0 and pm60 > 0 and pm10 > 0 and pavg10 > 0:
                strong = pc > pm20 > pm60 and pc / p20c - 1.0 >= 0.15
                shrink = pv < pavg10 * 0.8
                pullback = -0.05 <= pc / ind["close"].iloc[i - 2] - 1.0 <= 0.01 if i >= 2 else False
                above_m10 = pc > pm10
                if strong and shrink and pullback and above_m10:
                    gap = o[i] / pc - 1.0
                    # v1.7.598 修笔误: 板幅按股票代码判(旧代码误用日期前两位, 恒"20"→lim恒0.10,
                    # 创业/科创板高开8%~19%的样本全被错误剔除)
                    lim = 0.20 if str(code)[:2] in ("30", "68") else 0.10
                    if 0.03 <= gap < lim - 0.01 and keep("竞价弱转强", i):
                        r = _sim_right(o[i], o, h, c, m10, i, n)
                        if r: res["竞价弱转强"].append((r[0] - FEE, r[1], r[2]))
    return res


def _aggregate(name, rows):
    rets = np.array([r for r, sp, ef in rows]) * 100
    span = np.array([sp for r, sp, ef in rows], dtype=float)
    eff = np.array([ef for r, sp, ef in rows], dtype=float)
    net = float(rets.mean())
    avg_eff = float(eff.mean())
    cap_cost = float((ANNUAL * eff / YEAR_TD * 100).mean())
    w = rets[rets > 0]; ls = rets[rets <= 0]
    pf = float(w.sum() / -ls.sum()) if ls.sum() < 0 else 99.0
    return {
        "model_name": name, "n": int(len(rets)),
        "win_rate": round(float((rets > 0).mean() * 100), 1),
        "avg_span": round(float(span.mean()), 1),
        "avg_eff": round(avg_eff, 1),
        "net_mean": round(net, 2),
        "net_after_cost": round(net - cap_cost, 2),
        "annualized": round(net / avg_eff * YEAR_TD, 0) if avg_eff > 0 else 0,
        "pf": round(pf, 2),
    }


async def run_model_backtest_weekly():
    now = datetime.now()
    start_date = (now - pd.Timedelta(days=HALF_YEAR_DAYS)).strftime("%Y-%m-%d")
    run_date = now.strftime("%Y-%m-%d")
    client = data_fetcher._get_client()
    uni = await _build_universe(client)
    if not uni:
        logger.warning("[model_bt] 全A列表为空, 跳过")
        return
    logger.info(f"[model_bt] 全A {len(uni)} 只, 回测窗口起 {start_date}")

    sid_of = {name: sid for sid, name in MODELS}
    acc = {name: [] for _, name in MODELS}
    sem = asyncio.Semaphore(10)
    done = [0]

    async def work(code, sym):
        async with sem:
            df = await _fetch_kl(client, sym)
        if df is not None:
            try:
                for name, rows in _backtest_one(df, start_date, code=code).items():
                    if rows:
                        acc[name].extend(rows)
            except Exception:
                pass
        done[0] += 1
        if done[0] % 500 == 0:
            logger.info(f"[model_bt] {done[0]}/{len(uni)}")
            await asyncio.sleep(0)   # 让出事件循环

    await asyncio.gather(*[work(c, s) for c, s in uni], return_exceptions=True)

    out = []
    for _, name in MODELS:
        if acc[name]:
            row = _aggregate(name, acc[name])
            row["signal_id"] = sid_of[name]
            out.append(row)
    if out:
        await repository.save_model_backtest(run_date, start_date, out)
    logger.info(f"[model_bt] 完成 {run_date}: " + " ".join(
        f"{r['model_name']}({r['n']}笔/{r['win_rate']}%/年化{r['annualized']}%)" for r in out))
    return {"run_date": run_date, "models": out}
