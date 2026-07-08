# -*- coding: utf-8 -*-
"""5 分钟真实可成交口径回测引擎(后端服务) —— 网页 API 与选股回测 skill 共用同一份。

口径约定:
  - 形态前提(均线/主升浪/缩量/前高): 日线(模型本就定义在日线上, 无5分钟均线主升浪)
  - 触发判定 + 可成交性 + 入场价: 触发那一刻真实累计量/额过闸(U型外推全天量); 入场用日线前复权
  - 出场: 当日盘中最高/最低触及止损/卖半(盘中极值), 破均线按日收盘
关键: 日线表 cfzy_sys_kline_cache 是前复权, 5分钟表 cfzy_sys_kline_5m 是后复权 —— 价格统一用日线前复权,
      5分钟只取复权中性的「量/额」做触发闸门, 避免 scale 错配。
"""
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

from backend.services.signal_engine_indicators import compute_indicators
from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG
from backend.services.intraday_estimator import project_full_day_volume
from backend.services.signal_engine_detectors import (
    _detect_platform_breakout, _detect_vol_breakout,
    _detect_rally_ma20_pullback, _detect_s0_weak_extreme,
    _detect_strong_start_right,
)
from backend.models.repo._db import _fetchall

MIN_BARS = 65
DEDUP_DAYS = 10
FEE = 0.003

# 模型注册表: 检测器 + 默认配置键 + 入场口径 + 各自真实出场规则(对齐模型图鉴生产口径)
#   exit: hard止损 / target卖半(None=不卖半) / cap封顶交易日 / ma均线列(None=不按均线) / ma_mult
_REG = [
    # v1.7.593: 回踩MA10/MA20/缩量突破 出场对齐实盘 B5 口径(v1.7.584 剩半沿5日线飘: 剩半收盘破MA5清,
    #   回踩MA20 同步从旧+15%/-7%/T15 统一到 +7%/-6%/T10) — 此前仅胜率重算(model_winrate_refresher)已切,
    #   这里是模型回测页/skill 口径源, 补齐对齐(台账「图鉴统一更新清单③」)。
    {"id": "BUY_RALLY_MA10", "name": "回踩MA10", "det": _detect_rally_ma20_pullback, "use_s0": False,
     "entry": "breakout", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}},
    {"id": "BUY_RALLY_MA20", "name": "回踩MA20", "det": _detect_rally_ma20_pullback, "use_s0": False,
     "entry": "breakout", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}},
    # v1.7.593 回踩MA60(中线六二法60日档): 全市场双窗 挖掘221笔62.4%/PF3.25, 独立样本243笔50.6%/PF1.96
    {"id": "BUY_RALLY_MA60", "name": "回踩MA60", "det": _detect_rally_ma20_pullback, "use_s0": False,
     "entry": "breakout", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}},
    {"id": "BUY_VOL_BREAKOUT", "name": "缩量突破", "det": _detect_vol_breakout, "use_s0": False,
     "entry": "breakout", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}},
    {"id": "BUY_PLATFORM_BREAKOUT", "name": "平台突破", "det": _detect_platform_breakout, "use_s0": False,
     "entry": "close", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma10", "ma_mult": 0.98}},
    {"id": "BUY_STRONG_START", "name": "强势起点", "det": _detect_strong_start_right, "use_s0": True,
     "entry": "close", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma10", "ma_mult": 0.98}},
    {"id": "BUY_WEAK_EXTREME", "name": "弱势极限", "det": _detect_s0_weak_extreme, "use_s0": False,
     "entry": "close", "exit": {"hard": -0.12, "target": None, "cap": 15, "ma": None, "ma_mult": 0.98}},
]
_REG_BY_ID = {m["id"]: m for m in _REG}
MODEL_IDS = [m["id"] for m in _REG]
MODEL_NAMES = {m["id"]: m["name"] for m in _REG}


def build_model(model_id, temp_config=None):
    """按 model_id 构造可回测的 model 对象; temp_config(可选)= {signal_id: {param: val}} 临时覆盖默认参数。
    返回 None 表示未知 model_id。"""
    reg = _REG_BY_ID.get(model_id)
    if reg is None:
        return None
    tc = temp_config or {}
    cfg = dict(DEFAULT_SIGNAL_CONFIG[model_id])
    cfg.update(tc.get(model_id, {}))
    s0 = None
    if reg["use_s0"]:
        s0 = dict(DEFAULT_SIGNAL_CONFIG["BUY_WEAK_EXTREME"])
        s0.update(tc.get("BUY_WEAK_EXTREME", {}))
    return {**reg, "cfg": cfg, "s0": s0}


def _call(det, sub, latest, cfg, s0):
    return det(sub, latest, cfg) if s0 is None else det(sub, latest, cfg, s0)


def daily_could_fire(model, sub, row):
    """日线全天量口径下能否触发(乐观必要条件): 不能则当日 intraday 也必不触发, 可安全跳过加速。"""
    latest = row.copy()
    latest["amount_now"] = float(row.get("amount_est", 0) or 0)
    return _call(model["det"], sub, latest, model["cfg"], model["s0"]) is not None


# ---------- 数据加载(逐只, 流式, 避免全市场一次性吃满内存) ----------
def _rows_to_df(rows):
    if len(rows) < MIN_BARS + 5:
        return None
    df = pd.DataFrame(rows).rename(columns={"trade_date": "date"})
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


async def load_daily_one(code):
    rows = await _fetchall(
        "SELECT trade_date,open,high,low,close,volume FROM cfzy_sys_kline_cache "
        "WHERE code=%s ORDER BY trade_date", (code,))
    return _rows_to_df(rows)


async def load_daily_many(codes, chunk=200):
    """批量加载多只日线(分块 IN 查询), 返回 {code: df}。用于自选股快测同步路径提速。"""
    out = {}
    for k in range(0, len(codes), chunk):
        part = codes[k:k + chunk]
        ph = ",".join(["%s"] * len(part))
        rows = await _fetchall(
            f"SELECT code,trade_date,open,high,low,close,volume FROM cfzy_sys_kline_cache "
            f"WHERE code IN ({ph}) ORDER BY code,trade_date", tuple(part))
        by = defaultdict(list)
        for r in rows:
            by[str(r["code"])].append(r)
        for c, rs in by.items():
            df = _rows_to_df(rs)
            if df is not None:
                out[c] = df
    return out


async def load_5m_one(code):
    """code → {date: [(分钟,high,low,close,volume,amount), ...]}"""
    rows = await _fetchall(
        "SELECT dt,high,low,close,volume,amount FROM cfzy_sys_kline_5m WHERE code=%s ORDER BY dt", (code,))
    byday = defaultdict(list)
    for r in rows:
        dt = r["dt"]
        byday[dt.strftime("%Y-%m-%d")].append(
            (dt.hour * 60 + dt.minute, float(r["high"] or 0), float(r["low"] or 0),
             float(r["close"] or 0), float(r["volume"] or 0), float(r["amount"] or 0)))
    return byday


async def load_names(codes):
    """代码→名称。先查全市场名称表 cfzy_sys_stock_names(覆盖全A), 再用自选股池
    cfzy_biz_stock_pool 的名补充/覆盖(池里人工维护的名更可信)。两表都缺则进度提示退回纯代码。"""
    out = {}
    for k in range(0, len(codes), 500):
        part = codes[k:k + 500]
        ph = ",".join(["%s"] * len(part))
        # 1) 全市场名称表(基底)
        try:
            rows = await _fetchall(
                f"SELECT code, name FROM cfzy_sys_stock_names WHERE code IN ({ph}) AND name<>''",
                tuple(part))
            for r in rows:
                out[str(r["code"])] = str(r["name"])
        except Exception:
            pass  # 表尚未建/未填充时静默退回, 不阻断回测
        # 2) 自选股池名覆盖(更可信)
        rows = await _fetchall(
            f"SELECT DISTINCT code, name FROM cfzy_biz_stock_pool WHERE code IN ({ph}) AND name<>''",
            tuple(part))
        for r in rows:
            out[str(r["code"])] = str(r["name"])
    return out


async def universe_codes(spec):
    """spec: 'all' | 'pool:<uid>' | 'code1,code2'. 返回与 5m 表有交集的代码列表。"""
    five = await _fetchall("SELECT DISTINCT code FROM cfzy_sys_kline_5m")
    have = set(str(r["code"]) for r in five)
    if spec == "all":
        return sorted(have)
    if spec.startswith("pool:"):
        uid = int(spec.split(":", 1)[1])
        prows = await _fetchall("SELECT DISTINCT code FROM cfzy_biz_stock_pool WHERE user_id=%s", (uid,))
        return [str(r["code"]) for r in prows if str(r["code"]) in have]
    return [c.strip() for c in spec.split(",") if c.strip() in have]


# ---------- 5 分钟触发 ----------
def fire_5m(model, sub, base_latest, day_bars, prev_close):
    """逐根注入盘中累计量/额(+U型外推全天量), 返回 (是否触发, 触发时真实累计额, 触发理由)。"""
    det, cfg, s0 = model["det"], model["cfg"], model["s0"]
    earliest = int(cfg.get("intraday_earliest_minute", 0) or 0)
    cum_vol = cum_amt = run_high = 0.0
    peak = 0.0
    for (mn, bh, bl, bc, bv, ba) in sorted(day_bars):
        cum_vol += bv
        cum_amt += ba
        run_high = max(run_high, bh)
        peak = cum_amt
        if mn < earliest:
            continue
        ndt = datetime(2000, 1, 1, mn // 60, mn % 60)
        latest = base_latest.copy()
        latest["high"] = max(run_high, float(base_latest.get("high") or 0))
        latest["volume"] = project_full_day_volume(cum_vol, ndt) or cum_vol
        latest["amount_now"] = cum_amt
        latest["amount_est"] = project_full_day_volume(cum_amt, ndt) or cum_amt
        reason = _call(det, sub, latest, cfg, s0)
        if reason is not None:
            return True, cum_amt, (reason if isinstance(reason, str) else "")
    return False, peak, ""


def entry_price(model, ind, i):
    """日线(前复权)入场价 — 与出场同 scale。breakout型=昨高×(1+突破%); 其余=当日收盘。"""
    row = ind.iloc[i]
    o = float(row["open"]); c = float(row["close"])
    if model.get("entry") == "breakout":
        bp = float(model["cfg"].get("breakout_pct", 2.0)) / 100
        prev_high = float(ind["high"].iloc[i - 1]) if i > 0 else 0.0
        trig = prev_high * (1 + bp)
        if trig <= 0:
            return c
        return o if o >= trig else trig
    return c


# ---------- 出场仿真(日线前复权) ----------
def simulate_exit_detail(entry, i, ind, exit_cfg, dates):
    """从买入次日起逐日: 盘中触及止损(当日最低≤止损价)/卖半(当日最高≥目标价), 破均线按日收盘, 封顶时停。
    返回单笔交易明细 dict(含出场机制/时间/价格/持股天数/净收益), entry≤0 或未来K线不足时返回 None。"""
    h = ind["high"].values; lo = ind["low"].values; c = ind["close"].values
    n = len(ind)
    hard, target = exit_cfg["hard"], exit_cfg["target"]
    cap, ma_col, ma_mult = exit_cfg["cap"], exit_cfg["ma"], exit_cfg["ma_mult"]
    ma = ind[ma_col].values if ma_col else None
    ma_label = "MA" + ma_col[2:] if ma_col else ""
    stop_px = entry * (1 + hard)
    tgt_px = entry * (1 + target) if target is not None else None
    if entry <= 0:
        return None
    last = i + cap
    if last > n - 1:
        return None

    half_leg = None
    if target is not None:
        half_leg = {"pos": 50, "reason": f"+{target * 100:.0f}% 卖半",
                    "price": round(float(entry * (1 + target)), 3),
                    "ret_pct": round(target * 100, 2)}   # 卖半腿毛收益=止盈目标

    def _mfe_mae(t):
        # 持有期(买入次日~出场日)价格相对买入价的最高浮盈/最大浮亏 + 各自第几个交易日
        seg_h = h[i + 1:t + 1]; seg_lo = lo[i + 1:t + 1]
        if len(seg_h) == 0:
            return 0.0, 0, 0.0, 0
        hi_rel = seg_h / entry - 1.0; lo_rel = seg_lo / entry - 1.0
        hk = int(np.argmax(hi_rel)); lk = int(np.argmin(lo_rel))
        return (round(float(hi_rel[hk]) * 100, 2), hk + 1,
                round(float(lo_rel[lk]) * 100, 2), lk + 1)

    def _d(ret, t, px, reason, took_half, half_date, half_t=None, rest_reason=None):
        # legs: 一次买入对应的卖出腿(卖半→清剩 两腿 / 非卖半 一腿), 供前端「出场明细」逐腿堆叠
        # 每腿带 ret_pct(该腿毛收益%) + hold(该腿持有交易日)
        exit_date = str(dates[t])[:10]
        rest_ret = round((float(px) / entry - 1.0) * 100, 2)
        if took_half and half_leg is not None:
            legs = [
                {**half_leg, "date": half_date, "hold": int((half_t or t) - i)},
                {"pos": 50, "reason": rest_reason or reason, "date": exit_date,
                 "price": round(float(px), 3), "ret_pct": rest_ret, "hold": int(t - i)},
            ]
        else:
            legs = [{"pos": 100, "reason": reason, "date": exit_date,
                     "price": round(float(px), 3), "ret_pct": rest_ret, "hold": int(t - i)}]
        mfe, mfe_d, mae, mae_d = _mfe_mae(t)
        return {"ret": ret, "exit_date": exit_date, "exit_price": float(px),
                "hold_days": int(t - i), "reason": reason,
                "took_half": took_half, "half_date": half_date, "legs": legs,
                "mfe_pct": mfe, "mfe_day": mfe_d, "mae_pct": mae, "mae_day": mae_d}

    half, rf, half_date, half_t = False, 0.0, None, None
    for t in range(i + 1, last + 1):
        if not half and lo[t] <= stop_px:
            return _d(hard - FEE, t, stop_px, f"止损 {hard * 100:+.0f}% 触发", False, None)
        if not half and tgt_px is not None and h[t] >= tgt_px:
            half, rf, half_date, half_t = True, target, str(dates[t])[:10], t
        if ma is not None and not np.isnan(ma[t]) and c[t] < ma[t] * ma_mult:
            if half:
                return _d(0.5 * rf + 0.5 * (c[t] / entry - 1.0) - FEE, t, c[t],
                          f"+{target * 100:.0f}% 卖半 → 跌破{ma_label}清剩", True, half_date, half_t,
                          rest_reason=f"清剩 跌破{ma_label}")
            return _d(c[t] / entry - 1.0 - FEE, t, c[t], f"跌破{ma_label}", False, None)
    cl = c[last]
    if half:
        return _d(0.5 * rf + 0.5 * (cl / entry - 1.0) - FEE, last, cl,
                  f"+{target * 100:.0f}% 卖半 → 持有满T+{cap}时停", True, half_date, half_t,
                  rest_reason=f"清剩 持有满T+{cap}时停")
    return _d(cl / entry - 1.0 - FEE, last, cl, f"持有满T+{cap}时停", False, None)


def simulate_exit(entry, i, ind, exit_cfg):
    """兼容旧接口: 只返回净收益(早期 skill/测试直用)。明细走 simulate_exit_detail。"""
    d = simulate_exit_detail(entry, i, ind, exit_cfg, ind["date"].astype(str).values)
    return None if d is None else d["ret"]


# ---------- 统计 ----------
def stat(rets):
    if not rets:
        return {"n": 0, "win": 0.0, "avg": 0.0, "pf": 0.0}
    a = np.array(rets)
    w = a[a > 0]; l = a[a <= 0]
    pf = (w.sum() / -l.sum()) if l.sum() < 0 else 99.0
    return {"n": int(len(a)), "win": round(float((a > 0).mean() * 100), 1),
            "avg": round(float(a.mean() * 100), 2), "pf": round(float(pf), 2)}


def fmt_stat(s):
    return f"n={s['n']:>4}  胜率={s['win']:>5.1f}%  均收={s['avg']:>+6.2f}%  PF={s['pf']:>5.2f}"


# ---------- 高层: 单模型回测(API/skill 共用; 支持日线/5分钟两种口径) ----------
async def run_model_backtest(model_id, codes, start, end, temp_config=None, monthly=True,
                             koujing="daily", preloaded_daily=None, progress_cb=None):
    """对单个模型在 codes×[start,end] 跑回测。

    Args:
      model_id: 如 'BUY_STRONG_START'
      codes: 代码列表(用 universe_codes() 解析 'all'/'pool:1'/...)
      start,end: 'YYYY-MM-DD'
      temp_config: 可选临时参数覆盖 {signal_id:{param:val}}
      koujing: 'daily'=日线全天量口径(快) / '5m'=5分钟真实可成交口径(慢, 揭穿日线高估)
      preloaded_daily: 可选 {code: df} 预加载日线(自选股快测同步路径用, 免逐只查询)
      progress_cb: 可选 async/sync 回调 (done, total, phase="", note="") 报进度
                   phase=当前阶段文案, note=当前正在处理的条目(如股票代码)
    Returns: {"model_id","model_name","koujing","overall":stat, "monthly":{ym:stat}, "scanned":N}
    """
    model = build_model(model_id, temp_config)
    if model is None:
        raise ValueError(f"未知模型 {model_id}")
    use_5m = (koujing == "5m")
    rets_all = []
    monthly_b = defaultdict(list)
    trades = []
    total = len(codes)
    phase = "5分钟逐只回测" if use_5m else "日线逐只回测"
    action = "加载5分钟K线·扫描可成交触发点" if use_5m else "扫描日线触发点·仿真出场"
    step = max(1, total // 100)          # ~100 次刷新; 小池子每只都报
    names = await load_names(codes)

    async def _emit(done, code):
        if not progress_cb:
            return
        if code:
            nm = names.get(code, "")
            note = f"{code} {nm} · {action}" if nm else f"{code} · {action}"
        else:
            note = ""
        r = progress_cb(done, total, phase=phase, note=note)
        if hasattr(r, "__await__"):
            await r

    await _emit(0, "")
    for done, code in enumerate(codes, 1):
        if done == 1 or done % step == 0 or done == total:
            await _emit(done, code)
        df = preloaded_daily.get(code) if preloaded_daily is not None else await load_daily_one(code)
        if df is None:
            continue
        ind = compute_indicators(df)
        ind["amount_est"] = ind["volume"] * ind["close"]
        dates = ind["date"].astype(str).values
        n = len(ind)
        day5m = await load_5m_one(code) if use_5m else None
        if use_5m and not day5m:
            continue
        last_dt = None
        for i in range(MIN_BARS, n):
            dstr = dates[i][:10]
            if dstr < start or dstr > end:
                continue
            sub = ind.iloc[:i + 1]; row = ind.iloc[i]
            # 日线口径: 检测器通过即触发(并取触发理由); 5分钟口径: 再过盘中真实量/额闸门
            latest_daily = row.copy()
            latest_daily["amount_now"] = float(row.get("amount_est", 0) or 0)
            daily_reason = _call(model["det"], sub, latest_daily, model["cfg"], model["s0"])
            if daily_reason is None:
                continue
            reason = daily_reason if isinstance(daily_reason, str) else ""
            if use_5m:
                bars = day5m.get(dstr)
                if not bars:
                    continue
                prev_close = float(ind["close"].iloc[i - 1])
                fired, _amt, _r = fire_5m(model, sub, row.copy(), bars, prev_close)
                if not fired:
                    continue
                if _r:
                    reason = _r
            pdt = pd.Timestamp(dstr)
            if last_dt is not None and (pdt - last_dt).days <= DEDUP_DAYS:
                last_dt = pdt
                continue
            last_dt = pdt
            ep = entry_price(model, ind, i)
            det = simulate_exit_detail(ep, i, ind, model["exit"], dates)
            if det is not None:
                rets_all.append(det["ret"])
                monthly_b[dstr[:7]].append(det["ret"])
                trades.append({
                    "code": code, "name": names.get(code, ""),
                    "buy_date": dstr, "model": model["name"], "detail": reason,
                    "buy_price": round(float(ep), 3),
                    "exit_reason": det["reason"], "exit_date": det["exit_date"],
                    "exit_price": round(det["exit_price"], 3),
                    "hold_days": det["hold_days"], "ret_pct": round(det["ret"] * 100, 2),
                    "took_half": det["took_half"], "legs": det.get("legs", []),
                    "mfe_pct": det["mfe_pct"], "mfe_day": det["mfe_day"],
                    "mae_pct": det["mae_pct"], "mae_day": det["mae_day"],
                })
    trades.sort(key=lambda t: t["buy_date"], reverse=True)   # 最近买入在前
    MAX_TRADES = 1000                                         # 防全市场超大 payload 拖垮浏览器
    out = {"model_id": model_id, "model_name": model["name"], "koujing": koujing,
           "overall": stat(rets_all), "scanned": total,
           "trades": trades[:MAX_TRADES], "trades_total": len(trades),
           "trades_truncated": len(trades) > MAX_TRADES}
    if monthly:
        out["monthly"] = {ym: stat(v) for ym, v in sorted(monthly_b.items())}
    return out


# 兼容旧名(skill 早期直测用过): 固定 5 分钟口径
async def run_backtest_5m(model_id, codes, start, end, temp_config=None, monthly=True, progress_cb=None):
    return await run_model_backtest(model_id, codes, start, end, temp_config, monthly,
                                    koujing="5m", progress_cb=progress_cb)
