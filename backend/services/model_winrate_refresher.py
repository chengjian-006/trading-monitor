"""每日收盘从本地全市场库(cfzy_sys_kline_cache)重算 5 个买入模型 近3月/近6月 胜率+单笔均收益。

口径与周回测(model_backtest_weekly)完全一致: 各模型自身真实出场规则 + 扣费0.30%,
胜率 = 单笔扣费后净收益 > 0 的占比; 单笔均收益 = 净收益均值。窗口为滚动近91天/近182天(按触发日)。
7 个买点模型(含竞价弱转强, 用日K模拟高开)。
CPU 重活(逐票检测)走线程池, 不阻塞事件循环。结果 upsert cfzy_biz_model_winrate, 供买入提醒带战绩。
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend.core.trading_calendar import is_workday
from backend.models import repository
from backend.models.repo._db import _fetchall
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG
from backend.services.model_backtest_weekly import (
    _sim_right, _sim_left, _sim_rally20, MODELS, WCFG, FEE, DEDUP, MIN_BARS,
)
from backend.services.signal_engine_detectors import (
    _detect_rally_ma20_pullback, _detect_vol_breakout,
    _detect_s0_weak_extreme, _detect_strong_start_right,
    _detect_platform_breakout,
)

logger = logging.getLogger(__name__)

WIN_3M, WIN_6M = 91, 182        # 滚动近3月/近6月(自然日)
LOAD_DAYS = 300                 # 多读些做指标预热 + 覆盖近6月窗

# 真股票前缀: 沪深主板/创业板/科创板。剔除指数码(98x/88x)/板块/北交所(4x/8x/92x)/期货主连(lc..)。
_STOCK_PREFIX = ("00", "30", "60", "68")


def _is_stock(code: str) -> bool:
    """仅保留真 A 股(沪深主板/创业/科创); 缓存里残留的指数/板块/北交所码一律剔除。"""
    return str(code)[:2] in _STOCK_PREFIX


def _anchor_date(rows: list[dict]) -> str:
    """全市场最大交易日(YYYY-MM-DD)。

    必须取全局 max, 不能用 ORDER BY code,trade_date 后的 rows[-1]——
    那是排最后的 code(如指数码 980030)的最后一根 K 线, 早就停更, 会把锚点冻在旧日期。
    """
    return max(str(r["trade_date"])[:10] for r in rows)


def _bt_one(df):
    """对一只票跑 5 模型, 返回 [(model_name, 'YYYY-MM-DD', ret_after_fee), ...]。纯CPU。"""
    out = []
    if df is None or len(df) < MIN_BARS + 5:
        return out
    ind = compute_indicators(df)
    ind["amount_est"] = ind["volume"] * ind["close"]
    vol_avg10 = ind["volume"].rolling(10, min_periods=5).mean().values
    o = ind["open"].values; h = ind["high"].values; c = ind["close"].values
    v = ind["volume"].values
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

    def day(i):
        return str(dts[i])[:10]

    for i in range(MIN_BARS, n):
        latest = ind.iloc[i]; sub = ind.iloc[:i + 1]
        if _detect_vol_breakout(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_VOL_BREAKOUT"]) and keep("缩量后放量突破", i):
            trig = ph[i] * 1.02; entry = o[i] if o[i] >= trig else trig
            r = _sim_right(entry, o, h, c, m10, i, n)
            if r: out.append(("缩量后放量突破", day(i), r[0] - FEE))
        if _detect_rally_ma20_pullback(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA20"]) and keep("回踩20MA缩量后突破昨高", i):
            trig = ph[i] * 1.025; entry = o[i] if o[i] >= trig else trig
            r = _sim_rally20(entry, o, h, c, m20, i, n)
            if r: out.append(("回踩20MA缩量后突破昨高", day(i), r[0] - FEE))
        if _detect_rally_ma20_pullback(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_RALLY_MA10"]) and keep("回踩10MA缩量后突破昨高", i):
            trig = ph[i] * 1.025; entry = o[i] if o[i] >= trig else trig
            r = _sim_right(entry, o, h, c, m10, i, n)
            if r: out.append(("回踩10MA缩量后突破昨高", day(i), r[0] - FEE))
        if not np.isnan(m10[i]) and _detect_strong_start_right(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_STRONG_START"], WCFG) and keep("强势起点", i):
            r = _sim_right(c[i], o, h, c, m10, i, n)
            if r: out.append(("强势起点", day(i), r[0] - FEE))
        if _detect_s0_weak_extreme(sub, latest, WCFG) and keep("弱势极限", i):
            r = _sim_left(c[i], c, i, n)
            if r: out.append(("弱势极限", day(i), r[0] - FEE))
        # 中继平台突破: 入场=触发日收盘(收盘确认口径), 出场同回踩MA10族
        if _detect_platform_breakout(sub, latest, DEFAULT_SIGNAL_CONFIG["BUY_PLATFORM_BREAKOUT"]) and keep("中继平台突破", i):
            r = _sim_right(c[i], o, h, c, m10, i, n)
            if r: out.append(("中继平台突破", day(i), r[0] - FEE))
        # 竞价弱转强: T-1强势缩量小回调 + T高开≥3%
        if i >= MIN_BARS and keep("竞价弱转强", i):
            prev = ind.iloc[i - 1]; pc = float(prev["close"])
            pv = float(prev["volume"])
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
                    lim = 0.20 if str(ind["date"].iloc[i])[:2] in ("30", "68") else 0.10
                    if 0.03 <= gap < lim - 0.01:
                        r = _sim_right(o[i], o, h, c, m10, i, n)
                        if r: out.append(("竞价弱转强", day(i), r[0] - FEE))
    return out


def _crunch(rows: list[dict], today_str: str) -> list[dict]:
    """逐票回测 + 按近3月/近6月桶聚合(纯CPU, 在线程池里跑)。"""
    by_code = defaultdict(list)
    for r in rows:
        by_code[str(r["code"])].append(r)
    base = datetime.strptime(today_str, "%Y-%m-%d")
    cut3 = (base - timedelta(days=WIN_3M)).strftime("%Y-%m-%d")
    cut6 = (base - timedelta(days=WIN_6M)).strftime("%Y-%m-%d")
    acc = {name: {"3m": [], "6m": []} for _, name in MODELS}
    for code, krows in by_code.items():
        if not _is_stock(code):   # 仅真A股; 剔指数码(98x/88x)/板块/北交所(ST无名暂不剔)
            continue
        df = pd.DataFrame(krows).rename(columns={"trade_date": "date"})
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        try:
            for name, d, ret in _bt_one(df):
                if d >= cut6:
                    acc[name]["6m"].append(ret)
                    if d >= cut3:
                        acc[name]["3m"].append(ret)
        except Exception:
            pass

    sid_of = {name: sid for sid, name in MODELS}

    def agg(lst):
        if not lst:
            return None, None, 0
        arr = np.array(lst)
        return round(float((arr > 0).mean() * 100), 1), round(float(arr.mean() * 100), 2), len(arr)

    out = []
    for _, name in MODELS:
        wr3, nt3, c3 = agg(acc[name]["3m"])
        wr6, nt6, c6 = agg(acc[name]["6m"])
        out.append({"signal_id": sid_of[name], "model_name": name,
                    "win_rate_3m": wr3, "net_3m": nt3, "n_3m": c3,
                    "win_rate_6m": wr6, "net_6m": nt6, "n_6m": c6})

    # 近3月胜率排名(仅有近3月样本的模型参与; 胜率高→第1名)
    ranked = sorted([o for o in out if o["n_3m"] and o["win_rate_3m"] is not None],
                    key=lambda x: x["win_rate_3m"], reverse=True)
    rank_n = len(ranked)
    for idx, o in enumerate(ranked, 1):
        o["rank_3m"] = idx
    for o in out:
        o.setdefault("rank_3m", None)
        o["rank_n"] = rank_n
    return out


async def refresh_model_winrate():
    """每日17:30(工作日): 读全市场日线缓存 → 线程池跑回测 → upsert 模型胜率表。"""
    if not is_workday(datetime.now()):
        return
    load_from = (datetime.now() - timedelta(days=LOAD_DAYS)).strftime("%Y-%m-%d")
    rows = await _fetchall(
        "SELECT code, trade_date, open, high, low, close, volume FROM cfzy_sys_kline_cache "
        "WHERE trade_date >= %s ORDER BY code, trade_date",
        (load_from,),
    )
    if not rows:
        logger.warning("[model_winrate] 全市场日线缓存为空, 跳过")
        return
    today_str = _anchor_date(rows)   # 缓存内最新交易日(全局max, 不取rows[-1]防指数码毒化)
    out = await asyncio.to_thread(_crunch, rows, today_str)
    await repository.save_model_winrate(today_str, out)
    logger.info("[model_winrate] 重算完成 截至%s: " % today_str + " ".join(
        f"{r['model_name']}(3月{r['win_rate_3m']}%/{r['n_3m']}笔, 6月{r['win_rate_6m']}%/{r['n_6m']}笔)"
        for r in out))
    return {"as_of": today_str, "models": out}
