# -*- coding: utf-8 -*-
"""5 分钟真实可成交口径回测引擎(后端服务) —— 网页 API、每日胜率重算 与 选股回测 skill 共用同一份。

口径约定(v1.7.598 诚实化):
  - 形态前提(均线/主升浪/缩量/前高): 日线(模型本就定义在日线上, 无5分钟均线主升浪)
  - 触发判定: fire_5m 逐根注入「该时刻已知」信息 —— close=当bar现价 / high=游程最高 /
    量额=累计+U型外推 / MA随现价增量修正 —— 复刻实时扫描器 _extract_indicators 的构造,
    彻底去掉「用全天收盘/全天最高筛交易」的前视偏差(旧口径系统性剔除盘中触发后走弱的失败样本)
  - 入场价: breakout族=昨高×(1+突破%)(开盘跳空则开盘价); 收盘确认族=触发时刻现价
  - 出场: 当日盘中最高/最低触及止损/卖半(盘中极值), 破均线按日收盘
  - 贴板不追(chase_limit): 回测传 code 后与实盘同拦(is_at_limit_up 纯函数, 板幅按代码)
关键: 日线表 cfzy_sys_kline_cache 是前复权, 5分钟表 cfzy_sys_kline_5m 是后复权(因子随分红除权变化) ——
      5分钟bar价格按日重定标(factor=当日日线收盘/当日末根5m收盘)到前复权刻度再用; 量/额复权中性直接用。
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
     "code_aware": True,
     "entry": "breakout", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma5", "ma_mult": 1.0}},
    {"id": "BUY_PLATFORM_BREAKOUT", "name": "平台突破", "det": _detect_platform_breakout, "use_s0": False,
     "code_aware": True,
     "entry": "close", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma10", "ma_mult": 0.98}},
    {"id": "BUY_STRONG_START", "name": "强势起点", "det": _detect_strong_start_right, "use_s0": True,
     "code_aware": True,
     "entry": "close", "exit": {"hard": -0.06, "target": 0.07, "cap": 10, "ma": "ma10", "ma_mult": 0.98}},
    # eod_honest: 收盘价入场+收盘检测的左侧模型 —— 无盘中触发抢跑, "诚实5分钟版"≡日线EOD版,
    # 故走快速EOD路径(不逐根5分钟扫描), 既省算力又是正确口径(前视偏差只存在于盘中触发的突破型)。
    {"id": "BUY_WEAK_EXTREME", "name": "弱势极限", "det": _detect_s0_weak_extreme, "use_s0": False,
     "entry": "close", "eod_honest": True,
     "exit": {"hard": -0.12, "target": None, "cap": 15, "ma": None, "ma_mult": 0.98}},
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


def _call(det, sub, latest, cfg, s0, code=None, name="", code_aware=False):
    """调检测器; code_aware 检测器(缩量突破/强势起点/平台突破)可传 code 启用贴板不追拦截,
    与实盘对称。code=None 时 is_at_limit_up 返回 False, 与旧回测口径兼容。"""
    args = (sub, latest, cfg) if s0 is None else (sub, latest, cfg, s0)
    if code_aware and code:
        return det(*args, code=code, name=name)
    return det(*args)


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


def _rows_to_byday(rows):
    byday = defaultdict(list)
    for r in rows:
        dt = r["dt"]
        byday[dt.strftime("%Y-%m-%d")].append(
            (dt.hour * 60 + dt.minute, float(r["high"] or 0), float(r["low"] or 0),
             float(r["close"] or 0), float(r["volume"] or 0), float(r["amount"] or 0)))
    return byday


async def load_5m_one(code):
    """code → {date: [(分钟,high,low,close,volume,amount), ...]}"""
    rows = await _fetchall(
        "SELECT dt,high,low,close,volume,amount FROM cfzy_sys_kline_5m WHERE code=%s ORDER BY dt", (code,))
    return _rows_to_byday(rows)


async def load_5m_days(code, days):
    """按候选日精准加载一只票的5分钟bar(单次往返, code 走 PK 前缀, 只回传命中日) —— 全市场
    胜率重算按需加载用, 避免每晚整表(5500万行)搬运。days: ['YYYY-MM-DD', ...]。"""
    if not days:
        return {}
    ph = ",".join(["%s"] * len(days))
    rows = await _fetchall(
        f"SELECT dt,high,low,close,volume,amount FROM cfzy_sys_kline_5m "
        f"WHERE code=%s AND DATE(dt) IN ({ph}) ORDER BY dt", (code, *days))
    return _rows_to_byday(rows)


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
def rescale_day_bars(day_bars, daily_close):
    """后复权5分钟bar → 日线前复权刻度。factor = 当日日线收盘 / 当日末根5m收盘。

    后复权因子随分红除权累积(如600519约7.5x), 直接混用会让突破/站位条件被虚高价击穿。
    价格字段×factor; 量/额复权中性不动。返回 (scaled_bars, factor)。"""
    bars = sorted(day_bars)
    if not bars:
        return [], 1.0
    last_close = float(bars[-1][3] or 0)
    if last_close <= 0 or not daily_close or daily_close <= 0:
        return bars, 1.0
    f = float(daily_close) / last_close
    return [(mn, bh * f, bl * f, bc * f, bv, ba) for (mn, bh, bl, bc, bv, ba) in bars], f


_MA_COLS = (("ma5", 5), ("ma10", 10), ("ma20", 20), ("ma60", 60))
_COARSE_STEP_MIN = 15   # 无价格下限的模型(弱势极限)按15分钟评估一次(累计仍每5m)


def _price_floor(model, sub):
    """当前bar游程最高 < 此价 时检测器必返 None(硬必要条件), 可跳过昂贵的检测器调用。

    突破/平台/强势起点的触发都要求"盘中最高(或涨幅)达到某价"——在到价之前逐根跑
    detect_main_rally 等全窗计算纯属浪费。返回 None 表示该模型无干净价格下限(弱势极限)。
    价格用前复权刻度, 与 rescale 后的 bar 同 scale。"""
    cfg = model["cfg"]
    mid = model["id"]
    if model.get("entry") == "breakout":
        ph = float(sub["high"].iloc[-2]) if len(sub) >= 2 else 0.0
        return ph * (1 + float(cfg.get("breakout_pct", 2.0)) / 100) if ph > 0 else None
    if mid == "BUY_PLATFORM_BREAKOUT":
        L = int(cfg.get("L", 8))
        if len(sub) < L + 1:
            return None
        PH = float(sub["high"].iloc[-(L + 1):-1].max())
        return PH * (1 + float(cfg.get("BUF", 0.005))) if PH > 0 else None
    if mid == "BUY_STRONG_START":
        pc = float(sub["close"].iloc[-2]) if len(sub) >= 2 else 0.0
        return pc * (1 + float(cfg.get("min_pct_change", 2.0)) / 100) if pc > 0 else None
    return None   # 弱势极限: 量能地量为主, 无价格下限 → 粗步长评估


def fire_5m_detail(model, sub, base_latest, day_bars, prev_close):
    """逐根注入「该时刻已知」信息(复刻实时扫描器构造), 返回
    (是否触发, 触发时真实累计额, 触发理由, 触发时现价(前复权), 触发分钟)。

    每根bar把 latest 改写成实时口径:
      close=当bar现价 / high=游程最高(含开盘) / volume·amount_est=U型外推 / amount_now=真实累计 /
      pct_change=现价涨幅 / MA5·10·20·60 增量修正 ma_k + (现价-全天收盘)/k
    sub 末行同步补丁(close/high/volume/amount_est), 供检测器的窗口统计(近N日量/主升浪)读到一致口径。
    旧口径的两处前视已除: close/MA 用全天收盘(v1.7.598前)、high 混入全天最高+后复权未重定标。

    性能(v1.7.599): 未到价格下限的bar直接跳过检测器(突破/平台/强势起点); 无下限的弱势极限
    按15分钟粗步长评估。据此把全市场重算从~8h压到可接受区间, 触发结果与逐根等价(下限是硬必要条件)。"""
    det, cfg, s0 = model["det"], model["cfg"], model["s0"]
    earliest = int(cfg.get("intraday_earliest_minute", 0) or 0)
    code = model.get("_code") or None
    code_aware = bool(model.get("code_aware"))
    c_full = float(base_latest.get("close") or 0)
    day_open = float(base_latest.get("open") or 0)
    bars, _f = rescale_day_bars(day_bars, c_full)
    ma_full = {}
    for k, _w in _MA_COLS:
        v = base_latest.get(k)
        ma_full[k] = float(v) if v is not None and pd.notna(v) else None
    # _eval_all=True → 精确模式: 逐根评估不跳过(研究/单测用); 生产默认走价格下限+粗步长快路。
    floor = None if model.get("_eval_all") else _price_floor(model, sub)
    eval_all = bool(model.get("_eval_all"))
    psub = sub.copy()
    rowpos = len(psub) - 1
    cols = psub.columns
    c_i = cols.get_loc("close"); h_i = cols.get_loc("high"); v_i = cols.get_loc("volume")
    a_i = cols.get_loc("amount_est") if "amount_est" in cols else -1
    cum_vol = cum_amt = 0.0
    run_high = day_open
    peak = 0.0
    last_eval_mn = -10 ** 9
    for (mn, bh, bl, bc, bv, ba) in bars:
        cum_vol += bv
        cum_amt += ba
        run_high = max(run_high, bh)
        peak = cum_amt
        if mn < earliest:
            continue
        if not eval_all:
            if floor is not None:
                if run_high < floor:             # 未到触发价, 检测器必不触发, 跳过
                    continue
            elif mn - last_eval_mn < _COARSE_STEP_MIN and mn != bars[-1][0]:
                continue                         # 无价格下限: 15分钟粗步长(末根必评)
        last_eval_mn = mn
        ndt = datetime(2000, 1, 1, mn // 60, mn % 60)
        vol_proj = project_full_day_volume(cum_vol, ndt) or cum_vol
        amt_proj = project_full_day_volume(cum_amt, ndt) or cum_amt
        latest = base_latest.copy()
        latest["close"] = bc
        latest["high"] = run_high
        latest["volume"] = vol_proj
        latest["amount_now"] = cum_amt
        latest["amount_est"] = amt_proj
        if prev_close and prev_close > 0:
            latest["pct_change"] = bc / prev_close - 1.0
        for k, w in _MA_COLS:
            if ma_full[k] is not None:
                latest[k] = ma_full[k] + (bc - c_full) / w
        psub.iat[rowpos, c_i] = bc
        psub.iat[rowpos, h_i] = run_high
        psub.iat[rowpos, v_i] = vol_proj
        if a_i >= 0:
            psub.iat[rowpos, a_i] = amt_proj
        reason = _call(det, psub, latest, cfg, s0, code=code, code_aware=code_aware)
        if reason is not None:
            return True, cum_amt, (reason if isinstance(reason, str) else ""), bc, mn
    return False, peak, "", None, None


def fire_5m(model, sub, base_latest, day_bars, prev_close):
    """兼容旧签名(选股 skill 直用): 返回 (是否触发, 触发时真实累计额, 触发理由)。"""
    fired, amt, reason, _px, _mn = fire_5m_detail(model, sub, base_latest, day_bars, prev_close)
    return fired, amt, reason


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


# ---------- 诚实5分钟扫描(候选日粗筛 + 逐根精判) ----------
def _candidate_day(model, cfg, i, h_arr, l_arr, c_arr, m10, m20, m60):
    """当日是否可能盘中触发 —— 只用「必要条件」粗筛(宁多勿漏, 精判交给 fire_5m_detail)。

    去掉旧的 daily_could_fire EOD 闸(它用全天收盘判站位/涨幅, 会漏掉盘中触发尾盘走弱的
    失败样本 = 前视偏差根源)。这里的条件全部是盘中触发的数学必要条件(或带宽容差的近似):
      breakout族: 盘中高点条件单调 → 当日最高 ≥ 昨高×(1+突破%) 是精确必要条件
      平台突破:   当日最高 ≥ 前L日平台上沿×(1+BUF)
      强势起点:   当日最高涨幅 ≥ min_pct_change
      弱势极限:   当日区间须触到锚线带(±容差再放宽1.5%抵消MA盘中漂移) 且 高点在MA60·MA20上方附近
    """
    mid = model["id"]
    if model.get("entry") == "breakout":
        bp = float(cfg.get("breakout_pct", 2.0)) / 100
        ph = h_arr[i - 1]
        return ph > 0 and h_arr[i] >= ph * (1 + bp)
    if mid == "BUY_PLATFORM_BREAKOUT":
        L = int(cfg.get("L", 8))
        if i < L + 1:
            return False
        PH = float(np.nanmax(h_arr[i - L:i]))
        return PH > 0 and h_arr[i] >= PH * (1 + float(cfg.get("BUF", 0.005)))
    if mid == "BUY_STRONG_START":
        pc = c_arr[i - 1]
        return pc > 0 and h_arr[i] / pc - 1.0 >= float(cfg.get("min_pct_change", 2.0)) / 100
    if mid == "BUY_WEAK_EXTREME":
        ma60 = m60[i]; ma20 = m20[i]; ma10 = m10[i]
        if np.isnan(ma60) or ma60 <= 0 or np.isnan(ma20) or ma20 <= 0:
            return False
        if h_arr[i] < ma60 * 0.985 or h_arr[i] < ma20 * 0.985:   # close>MA60且>MA20 的必要近似
            return False
        slack_hi, slack_lo = 1.035, 0.965                          # ±2%带宽 + 1.5%漂移容差
        band10 = (not np.isnan(ma10)) and ma10 > 0 and \
            l_arr[i] <= ma10 * slack_hi and h_arr[i] >= ma10 * slack_lo
        band20 = l_arr[i] <= ma20 * slack_hi and h_arr[i] >= ma20 * slack_lo
        return band10 or band20
    return True


def candidate_days(model, ind, start, end):
    """一只票在 [start,end] 内该模型的候选触发日列表(YYYY-MM-DD) —— 供按需加载5分钟bar。"""
    dates = ind["date"].astype(str).values
    h_arr = ind["high"].values; l_arr = ind["low"].values; c_arr = ind["close"].values
    m10 = ind["ma10"].values; m20 = ind["ma20"].values; m60 = ind["ma60"].values
    cfg = model["cfg"]
    out = []
    for i in range(MIN_BARS, len(ind)):
        dstr = dates[i][:10]
        if dstr < start or dstr > end:
            continue
        if _candidate_day(model, cfg, i, h_arr, l_arr, c_arr, m10, m20, m60):
            out.append(dstr)
    return out


def eod_trades(model, ind, start, end, code="", name=""):
    """收盘价入场模型(弱势极限)的快速EOD日线扫描 —— 收盘检测+收盘入场, 无盘中前视, 与旧口径等价。

    不加载/不遍历5分钟bar: 逐日用全天数据判检测器, 触发即以收盘价入场, 出场走 _REG 规则。
    与 scan_trades_5m 返回同构 trade 明细。"""
    m = dict(model)
    dates = ind["date"].astype(str).values
    n = len(ind)
    trades = []
    last_dt = None
    for i in range(MIN_BARS, n):
        dstr = dates[i][:10]
        if dstr < start or dstr > end:
            continue
        sub = ind.iloc[:i + 1]; row = ind.iloc[i]
        latest = row.copy()
        latest["amount_now"] = float(row.get("amount_est", 0) or 0)
        reason = _call(m["det"], sub, latest, m["cfg"], m["s0"],
                       code=code or None, code_aware=bool(m.get("code_aware")))
        if reason is None:
            continue
        pdt = pd.Timestamp(dstr)
        if last_dt is not None and (pdt - last_dt).days <= DEDUP_DAYS:
            last_dt = pdt
            continue
        last_dt = pdt
        ep = float(row["close"])
        det = simulate_exit_detail(ep, i, ind, m["exit"], dates)
        if det is None:
            continue
        trades.append({
            "code": code, "name": name,
            "buy_date": dstr, "model": m["name"],
            "detail": reason if isinstance(reason, str) else "",
            "buy_price": round(ep, 3),
            "exit_reason": det["reason"], "exit_date": det["exit_date"],
            "exit_price": round(det["exit_price"], 3),
            "hold_days": det["hold_days"], "ret_pct": round(det["ret"] * 100, 2),
            "took_half": det["took_half"], "legs": det.get("legs", []),
            "mfe_pct": det["mfe_pct"], "mfe_day": det["mfe_day"],
            "mae_pct": det["mae_pct"], "mae_day": det["mae_day"],
            "ret": det["ret"],
        })
    return trades


def scan_trades_5m(model, ind, day5m, start, end, code="", name=""):
    """一只票单模型 5分钟诚实口径扫描 → 交易明细列表(与 run_model_backtest trades 同构)。

    day5m: {YYYY-MM-DD: [(mn,h,l,c,vol,amt), ...]} 后复权bar(内部按日重定标)。
    入场: breakout族=昨高×(1+突破%)(跳空则开盘); 收盘确认族=触发时刻现价。
    去重: 同模型 DEDUP_DAYS 内不重复开仓(与旧口径一致)。"""
    m = dict(model)
    m["_code"] = code or None
    dates = ind["date"].astype(str).values
    h_arr = ind["high"].values; l_arr = ind["low"].values; c_arr = ind["close"].values
    m10 = ind["ma10"].values; m20 = ind["ma20"].values; m60 = ind["ma60"].values
    cfg = m["cfg"]
    n = len(ind)
    trades = []
    last_dt = None
    for i in range(MIN_BARS, n):
        dstr = dates[i][:10]
        if dstr < start or dstr > end:
            continue
        bars = day5m.get(dstr)
        if not bars:
            continue
        if not _candidate_day(m, cfg, i, h_arr, l_arr, c_arr, m10, m20, m60):
            continue
        sub = ind.iloc[:i + 1]
        prev_close = float(c_arr[i - 1]) if i > 0 else 0.0
        fired, _amt, reason, trig_px, _mn = fire_5m_detail(m, sub, ind.iloc[i].copy(), bars, prev_close)
        if not fired:
            continue
        pdt = pd.Timestamp(dstr)
        if last_dt is not None and (pdt - last_dt).days <= DEDUP_DAYS:
            last_dt = pdt
            continue
        last_dt = pdt
        if m.get("entry") == "breakout":
            ep = entry_price(m, ind, i)
        else:
            ep = trig_px if trig_px and trig_px > 0 else float(ind["close"].iloc[i])
        det = simulate_exit_detail(ep, i, ind, m["exit"], dates)
        if det is None:
            continue
        trades.append({
            "code": code, "name": name,
            "buy_date": dstr, "model": m["name"], "detail": reason,
            "buy_price": round(float(ep), 3),
            "exit_reason": det["reason"], "exit_date": det["exit_date"],
            "exit_price": round(det["exit_price"], 3),
            "hold_days": det["hold_days"], "ret_pct": round(det["ret"] * 100, 2),
            "took_half": det["took_half"], "legs": det.get("legs", []),
            "mfe_pct": det["mfe_pct"], "mfe_day": det["mfe_day"],
            "mae_pct": det["mae_pct"], "mae_day": det["mae_day"],
            "ret": det["ret"],
        })
    return trades


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
        if use_5m:
            # 诚实口径(v1.7.598): 候选日粗筛 + 逐根真实注入, 不再走 EOD 日线闸(前视偏差根源);
            # 传 code 使贴板不追与实盘对称。收盘入场的弱势极限无盘中前视 → 走快速EOD路径不加载5分钟。
            if model.get("eod_honest"):
                emitted = eod_trades(model, ind, start, end, code=code, name=names.get(code, ""))
            else:
                day5m = await load_5m_one(code)
                if not day5m:
                    continue
                emitted = scan_trades_5m(model, ind, day5m, start, end,
                                         code=code, name=names.get(code, ""))
            for t in emitted:
                ret = t.pop("ret")
                rets_all.append(ret)
                monthly_b[t["buy_date"][:7]].append(ret)
                trades.append(t)
            continue
        last_dt = None
        for i in range(MIN_BARS, n):
            dstr = dates[i][:10]
            if dstr < start or dstr > end:
                continue
            sub = ind.iloc[:i + 1]; row = ind.iloc[i]
            # 日线口径(快, 乐观): 检测器用全天数据, 通过即触发
            latest_daily = row.copy()
            latest_daily["amount_now"] = float(row.get("amount_est", 0) or 0)
            daily_reason = _call(model["det"], sub, latest_daily, model["cfg"], model["s0"])
            if daily_reason is None:
                continue
            reason = daily_reason if isinstance(daily_reason, str) else ""
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
