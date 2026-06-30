"""临近买点 接近度评估 — 短线盯盘 v1.7.x.

给一只票的日K(+实时报价)算它当前距四个买点有多近, 分两档:
  触发 — 引擎 detect_signals 当前就命中该买点(口径 100% 同实时扫描/推送);
  接近 — 还没触发, 但已贴近相关均线(主升浪在 + 真贴线), 只差量能或站位。

四个买点:
  弱势极限(左)   BUY_WEAK_EXTREME   主升浪回踩+绝对地量+相对缩量+站上MA20/MA60+贴近MA10/MA20
  回踩10MA缩量后突破昨高(右)   BUY_RALLY_MA10     主升浪+今日贴MA10(±1%)+缩量, 等明日突破昨高2.5%
  回踩20MA缩量后突破昨高(右)   BUY_RALLY_MA20     主升浪+今日贴MA20(±3%)+缩量, 等明日突破昨高2.5%
  中继平台突破(右)   BUY_PLATFORM_BREAKOUT   窄平台+前置主升结构成立, 现价逼近上沿(还差≤3%); 触发要尾盘收盘确认故提前报接近
  强势起点(右)   BUY_STRONG_START   左侧地量后今日放量站均线(当日触发型, 只报触发不报接近)

阈值全部读 signal_engine_config 的合并配置, 与实时引擎保持同一口径; 接近档的"贴线带"
(WE_NEAR / R10_NEAR / R20_NEAR / PB_NEAR)是本模块独有的窗口, v1.7.534 起≈触发容差本身
(原放宽~1.5倍, 范围太广), 语义=已真贴在线上、只差量能或一根突破。
"""
import logging

import numpy as np
import pandas as pd

from backend.services import signal_engine
from backend.services.signal_engine_indicators import compute_indicators
from backend.services.trading_concepts import detect_main_rally

logger = logging.getLogger(__name__)

# 四买点 id → 中文名 (含强势起点, 仅触发档用)
BUY_NAMES = {
    "BUY_WEAK_EXTREME": "弱势极限",
    "BUY_RALLY_MA10": "回踩10MA缩量后突破昨高",
    "BUY_RALLY_MA20": "回踩20MA缩量后突破昨高",
    "BUY_STRONG_START": "强势起点",
    "BUY_VOL_BREAKOUT": "缩量后放量突破",
    "BUY_PLATFORM_BREAKOUT": "中继平台突破",   # 尾盘14:40收盘确认才触发, 但接近档全天报"逼近上沿" (v1.7.446)
}

# 接近档"贴线带" — v1.7.534 收紧到≈触发容差本身(原放宽1.5倍范围太广, 17只→8-10只):
# 语义改为"已真贴在均线/上沿上、只差量能或一根突破", 而非"在向均线靠近的路上"。
WE_NEAR_PCT = 2.0    # 弱势极限锚点(MA10∪MA20)触发±2% → 近≤2%(原3.0)
R10_NEAR_PCT = 1.5   # 回踩10MA缩量后突破昨高 → 近≤1.5%(原2.0)
R20_NEAR_PCT = 3.0   # 回踩20MA缩量后突破昨高 触发±3% → 近≤3%(原4.5)
PB_NEAR_PCT = 2.0    # 中继平台突破: 现价距突破价(上沿×1.005)还差≤2% = 逼近上沿(原3.0)


def _overlay_rt(df: pd.DataFrame, rt: dict | None) -> pd.DataFrame:
    """把实时报价覆盖到最后一根K线的 close/volume(同 scanner._extract_indicators)。"""
    d = compute_indicators(df)
    if rt and rt.get("price", 0) > 0:
        from backend.services.signal_engine import _ensure_today_bar
        d = _ensure_today_bar(d, rt)   # v1.7.384: 末根=昨日时追加今日行, 防覆盖昨日真实K线
        i = d.index[-1]
        d.loc[i, "close"] = rt["price"]
        if rt.get("volume"):
            d.loc[i, "volume"] = rt["volume"]
        d = compute_indicators(d)
    return d


def _rally_state(d: pd.DataFrame, within_bars: int):
    """主升浪前置: (达标?, 距峰交易日, 峰值涨幅%). 口径同检测器(截至今日)。"""
    r = detect_main_rally(d)
    if not r.ever_qualified or r.peak_idx is None:
        return False, None, None
    bars = len(d) - 1 - r.peak_idx
    return bars <= within_bars, bars, r.peak_gain_pct * 100


def _eval_weak_extreme(d: pd.DataFrame, sc: dict) -> dict:
    """弱势极限接近度(6 条 AND, 与 _detect_s0_weak_extreme 同口径)。"""
    latest = d.iloc[-1]
    close, ma10, ma20, ma60 = (float(latest[k]) for k in ("close", "ma10", "ma20", "ma60"))
    win = int(sc.get("vol_floor_window", 10))
    vols = d.tail(win)["volume"]
    vmin, vavg = float(vols.min()), float(vols.mean())
    vtoday = float(latest["volume"])
    r_ok, bars, gain = _rally_state(d, int(sc.get("rally_peak_within_bars", 30)))

    vmin_ratio = vtoday / vmin if vmin > 0 else 99.0
    vavg_ratio = vtoday / vavg if vavg > 0 else 99.0
    dist10 = (close - ma10) / ma10 * 100 if ma10 > 0 else 999
    dist20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 999
    tol10 = float(sc.get("ma10_below_max_pct", 2.0))
    tol20 = float(sc.get("ma20_below_max_pct", 2.0))
    near10 = -tol10 <= dist10 <= float(sc.get("ma10_above_max_pct", 2.0))
    near20 = -tol20 <= dist20 <= float(sc.get("ma20_above_max_pct", 2.0))
    anchor = "MA10" if abs(dist10) <= abs(dist20) else "MA20"
    adist = dist10 if anchor == "MA10" else dist20
    vft = float(sc.get("vol_floor_tolerance", 1.0))
    vsr = float(sc.get("vol_shrink_avg10_ratio", 0.70))

    # 每条带当前值/需达阈值, 给"还差"摆量化数据用
    checks = [
        {"name": "主升浪回踩", "ok": r_ok, "cur": "" if r_ok else "无主升浪前置", "need": ""},
        {"name": "绝对地量", "ok": vmin_ratio <= vft, "cur": f"{vmin_ratio:.2f}倍{win}日最低量", "need": f"≤{vft}倍"},
        {"name": "相对缩量", "ok": vavg_ratio <= vsr, "cur": f"{vavg_ratio:.2f}倍{win}日均量", "need": f"≤{vsr}倍"},
        {"name": "站上MA60", "ok": close > ma60, "cur": f"距MA60{(close - ma60) / ma60 * 100:+.1f}%" if ma60 > 0 else "—", "need": "站上"},
        {"name": "站上MA20", "ok": close > ma20, "cur": f"距MA20{dist20:+.1f}%", "need": "站上"},
        {"name": "贴近MA10/MA20", "ok": near10 or near20, "cur": f"距{anchor}{adist:+.1f}%", "need": f"±{tol10:.0f}%"},
    ]
    note = (f"距{anchor}{adist:+.1f}% 量{vavg_ratio:.2f}倍均/{vmin_ratio:.2f}倍最低"
            + (f" 主升浪+{gain:.0f}%距峰{bars}日" if r_ok else " 无主升浪前置"))
    return {"checks": checks, "score": sum(1 for c in checks if c["ok"]), "anchor_dist": abs(adist),
            "rally_ok": r_ok, "note": note, "near_pct": WE_NEAR_PCT, "min_score": 5}


def _eval_rally(d: pd.DataFrame, sc: dict, anchor: str, near_pct: float) -> dict:
    """回踩MAxx接近度(把今日当 setup, 等明日突破; 与 _detect_rally_ma20_pullback 同口径)。
    anchor='ma10'/'ma20'。"""
    latest = d.iloc[-1]
    close = float(latest["close"])
    maval = float(latest.get(anchor) or 0)
    touch = float(sc.get("ma20_touch_pct", 3.0))
    win = int(sc.get("amount_avg_window", 10))
    vols = d.tail(win)["volume"]
    vavg = float(vols.mean())
    vtoday = float(latest["volume"])
    shrink = vtoday / vavg if vavg > 0 else 99.0
    amount_est = close * vtoday    # 新浪 volume 单位=股, 直接 ×收盘 ≈ 成交额
    r_ok, bars, gain = _rally_state(d, int(sc.get("rally_peak_within_bars", 30)))
    dist = (close - maval) / maval * 100 if maval > 0 else 999
    min_amt = float(sc.get("min_full_day_amount", 1_000_000_000))
    shrink_ratio = float(sc.get("shrink_ratio", 0.8))
    au = anchor.upper()

    # 每条带当前值/需达阈值, 给"还差"摆量化数据用
    checks = [
        {"name": "主升浪前置", "ok": r_ok, "cur": "" if r_ok else "无主升浪前置", "need": ""},
        {"name": f"回踩{au}", "ok": abs(dist) <= touch, "cur": f"距{au}{dist:+.1f}%", "need": f"±{touch:.0f}%"},
        {"name": "缩量", "ok": shrink < shrink_ratio, "cur": f"{shrink:.2f}倍{win}日均量", "need": f"<{shrink_ratio}倍"},
        {"name": "成交额", "ok": amount_est >= min_amt, "cur": f"{amount_est / 1e8:.1f}亿", "need": f"≥{min_amt / 1e8:.0f}亿"},
    ]
    note = (f"距{au}{dist:+.1f}% 量{shrink:.2f}倍均 额{amount_est/1e8:.1f}亿"
            + (f" 主升浪+{gain:.0f}%距峰{bars}日" if r_ok else " 无主升浪前置"))
    return {"checks": checks, "score": sum(1 for c in checks if c["ok"]), "anchor_dist": abs(dist),
            "rally_ok": r_ok, "note": note, "near_pct": near_pct, "min_score": 3}


def _eval_platform_breakout(d: pd.DataFrame, sc: dict) -> dict:
    """中继平台突破接近度 — 与 _detect_platform_breakout 同口径, 但量"距突破价还差多少".

    触发档要尾盘14:40收盘确认(现价≥上沿×1.005), 一旦突破常已涨停才报; 接近档全天评估:
    平台结构(窄平台+前置主升+缓升台阶)已成立, 现价逼近上沿(还差≤PB_NEAR_PCT)即提前报,
    放量/成交额盘中未到照样报(摆进"还差"), 等真突破时触发档自然接管。
    """
    L = int(sc.get("L", 12))
    NP = int(sc.get("N_PRIOR", 20))
    req_prior = bool(sc.get("REQ_PRIOR", True))
    req_rise = bool(sc.get("REQ_RISE", True))
    req_hold = bool(sc.get("REQ_HOLD", False))
    need = L + 1 + (NP if req_prior else 0)
    if len(d) < need:
        return {"checks": [], "score": 0, "anchor_dist": 99.0, "rally_ok": False,
                "note": "K线不足", "near_pct": PB_NEAR_PCT, "min_score": 99}

    latest = d.iloc[-1]
    close = float(latest["close"])
    plat = d.iloc[-(L + 1):-1]            # 平台窗 = 今日之前 L 根(不含今日)
    PH = float(plat["high"].max())
    ch = float(plat["close"].max()); cl = float(plat["close"].min())
    if cl <= 0 or PH <= 0:
        return {"checks": [], "score": 0, "anchor_dist": 99.0, "rally_ok": False,
                "note": "平台数据异常", "near_pct": PB_NEAR_PCT, "min_score": 99}
    amp = (ch - cl) / cl
    A = float(sc.get("A", 0.15))

    # 结构条件(setup_ok 全 AND): 窄平台 + 前置主升(可选) + 缓升台阶(可选) + 不破位(可选)
    checks = [{"name": "平台窄", "ok": amp <= A, "cur": f"振幅{amp * 100:.1f}%", "need": f"≤{A * 100:.0f}%"}]
    n_struct = 1

    if req_prior:
        prior = d.iloc[-(L + 1 + NP):-(L + 1)]
        prior_low = float(prior["low"].min()) if len(prior) else 0.0
        prior_gain = (PH - prior_low) / prior_low if prior_low > 0 else 0.0
        R = float(sc.get("R", 0.20))
        checks.append({"name": "中继前置主升", "ok": prior_gain >= R,
                       "cur": f"前置+{prior_gain * 100:.0f}%", "need": f"≥{R * 100:.0f}%"})
        n_struct += 1

    if req_rise:
        half = L // 2
        rise = 0.0
        rise_ok = False
        if half >= 1:
            med1 = float(plat["close"].iloc[:half].median())
            med2 = float(plat["close"].iloc[half:].median())
            if med1 > 0:
                rise = med2 / med1 - 1.0
                rmin, rmax = float(sc.get("RISE_MIN", 0.0)), float(sc.get("RISE_MAX", 0.05))
                rise_ok = rmin <= rise <= rmax
        checks.append({"name": "缓升台阶", "ok": rise_ok,
                       "cur": f"中位{rise * 100:+.1f}%", "need": "缓升不下倾"})
        n_struct += 1

    if req_hold:
        m = plat["ma20"]
        hold_ok = (not m.isna().any()) and bool((plat["close"] >= m * 0.95).all())
        checks.append({"name": "不破MA20", "ok": hold_ok, "cur": "", "need": "平台期不深破MA20"})
        n_struct += 1

    setup_ok = all(c["ok"] for c in checks)

    # 贴近上沿: 距突破价(上沿×(1+BUF))还差多少 — 已突破(≤0)归触发档, 接近档只看 (0, PB_NEAR_PCT]
    buf = float(sc.get("BUF", 0.005))
    lvl = PH * (1 + buf)
    gap = (lvl - close) / lvl * 100 if lvl > 0 else 99.0   # >0=还差才到突破价
    near_edge = 0.0 < gap <= PB_NEAR_PCT
    checks.append({"name": "逼近上沿", "ok": near_edge,
                   "cur": f"距突破价{lvl:.2f}还差{gap:+.1f}%", "need": f"≤{PB_NEAR_PCT:.0f}%"})

    # 放量 / 成交额(盘中常未到, 摆进"还差", 不卡接近门槛)
    if bool(sc.get("REQ_VOL", True)):
        pav = float(plat["volume"].mean())
        tvol = float(latest["volume"])
        V = float(sc.get("V", 1.2))
        ratio = tvol / pav if pav > 0 else 0.0
        checks.append({"name": "放量", "ok": pav > 0 and tvol >= pav * V,
                       "cur": f"{ratio:.1f}倍平台均", "need": f"≥{V}倍"})
    est_amount = close * float(latest["volume"])   # 新浪 volume=股, ×现价≈累计成交额
    min_amt = float(sc.get("min_full_day_amount", 1_000_000_000))
    checks.append({"name": "成交额", "ok": est_amount >= min_amt,
                   "cur": f"{est_amount / 1e8:.1f}亿", "need": f"≥{min_amt / 1e8:.0f}亿"})

    anchor_dist = gap if gap > 0 else 99.0
    note = f"距突破价{lvl:.2f}还差{gap:+.1f}% 平台{L}日(振幅{amp * 100:.1f}%)"
    # 接近门槛: 结构成立(setup_ok)且逼近上沿 → score 必 ≥ n_struct+1; 放量/额缺位不挡
    return {"checks": checks, "score": sum(1 for c in checks if c["ok"]),
            "anchor_dist": round(anchor_dist, 2), "rally_ok": setup_ok,
            "note": note, "near_pct": PB_NEAR_PCT, "min_score": n_struct + 1}


def _fmt_miss(c: dict) -> str:
    """未满足条件 → 带量化差距的文案, 如 "缩量(当前1.53x10日均·需<0.8x)"。"""
    if c.get("cur") and c.get("need"):
        return f"{c['name']}(当前{c['cur']}·需{c['need']})"
    if c.get("cur"):
        return f"{c['name']}({c['cur']})"
    return c["name"]


def evaluate(df: pd.DataFrame, rt: dict | None, cfg: dict) -> dict | None:
    """评估一只票的临近买点。

    Args:
        df:  原始日K (date/open/high/low/close/volume), 需 ≥65 根.
        rt:  实时报价 (price/volume/pct_change), 可空(盘后/非交易日走K线收盘).
        cfg: signal_engine_config.get_merged_config(user_config) 合并后配置.

    Returns:
        {tier, dist, hits:[{kind, buy_id, buy_name, note, miss[]}]} 或 None(不接近任一买点).
        tier: 2=有触发, 1=仅接近.
    """
    if df is None or df.empty or len(df) < 65:
        return None
    d = _overlay_rt(df, rt)
    if pd.isna(d.iloc[-1].get("ma60", np.nan)):
        return None

    # 触发档: 完全复用引擎 detect_signals(口径 100% 同实时扫描/推送)
    triggered: dict[str, str] = {}
    try:
        for sig in signal_engine.detect_signals(df, "short", rt, None):
            if sig.signal_id in BUY_NAMES and sig.direction == "buy":
                triggered[sig.signal_id] = sig.detail
    except Exception as e:
        logger.debug(f"[near_buy] detect_signals 失败: {e}")

    # 接近档: 三个 setup 型买点的贴线评估(强势起点是当日放量触发型, 不算接近)
    evals = {
        "BUY_WEAK_EXTREME": _eval_weak_extreme(d, cfg.get("BUY_WEAK_EXTREME", {})),
        "BUY_RALLY_MA10": _eval_rally(d, cfg.get("BUY_RALLY_MA10", {}), "ma10", R10_NEAR_PCT),
        "BUY_RALLY_MA20": _eval_rally(d, cfg.get("BUY_RALLY_MA20", {}), "ma20", R20_NEAR_PCT),
        "BUY_PLATFORM_BREAKOUT": _eval_platform_breakout(d, cfg.get("BUY_PLATFORM_BREAKOUT", {})),
    }

    hits: list[dict] = []
    dist = 99.0
    for buy_id, name in BUY_NAMES.items():
        if buy_id in triggered:
            ev = evals.get(buy_id)
            d_anchor = ev["anchor_dist"] if ev else 0.0
            total = len(ev["checks"]) if ev and ev.get("checks") else 0
            band = float(ev["near_pct"]) if ev else 0.0
            # 触发 = 已到/越线且条件全满足: 贴线度满格(dist_pct=0)、条件全绿(met=total)
            hits.append({"kind": "触发", "buy_id": buy_id, "buy_name": name,
                         "note": triggered[buy_id], "miss": [],
                         "dist_pct": 0.0, "band_pct": round(band, 2),
                         "met": total, "total": total})
            dist = min(dist, d_anchor)

    if not hits:  # 没有任何触发, 再看接近(避免触发票被接近档重复标注)
        for buy_id, ev in evals.items():
            if buy_id in triggered:
                continue
            near = (ev["rally_ok"] and ev["score"] >= ev["min_score"]
                    and ev["anchor_dist"] <= ev["near_pct"])
            if near:
                miss = [_fmt_miss(c) for c in ev["checks"] if not c["ok"]]
                # 可视化数值: 贴线度(dist_pct/band_pct)+条件满足(met/total), 供前端画进度条+圆点
                hits.append({"kind": "接近", "buy_id": buy_id, "buy_name": BUY_NAMES[buy_id],
                             "note": ev["note"], "miss": miss,
                             "dist_pct": round(float(ev["anchor_dist"]), 2),
                             "band_pct": round(float(ev["near_pct"]), 2),
                             "met": int(ev["score"]), "total": len(ev["checks"])})
                dist = min(dist, ev["anchor_dist"])

    if not hits:
        return None
    tier = 2 if any(h["kind"] == "触发" for h in hits) else 1
    return {"tier": tier, "dist": round(dist, 2), "hits": hits}
