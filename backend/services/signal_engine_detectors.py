"""信号引擎 — 检测器 + 形态/量价 helper - v1.7.x.

主要检测器:
  _detect_s0_weak_extreme     : 弱势极限(左侧)
  _detect_strong_start_right  : 强势起点(右侧)
  _detect_s3_rally_pullback   : S3 老版主升浪回踩 (历史保留, 仅给 BacktestView 用)

形态/量价 helper (可被多检测器复用):
  _count_ma5_uptrend_days
  _is_first_touch_after_rally
  _vol_ratio_to_recent_peak
  _consolidation_days_near_ma
  _consolidation_low_near_ma
  _nearest_ma_label
  _intraday_after             : 盘中早盘门槛 (含集合竞价 09:00-09:30 修复)

工具:
  get_stock_ma_status         : 给一只票打 MA 位置标签 (强势/震荡/偏弱/极弱)
"""
from typing import Optional

import numpy as np
import pandas as pd

from backend.services.intraday_estimator import is_intraday
from backend.utils.limit_calc import is_at_limit_up


# ── 形态/量价 helper ──

def _count_ma5_uptrend_days(d: pd.DataFrame) -> int:
    if len(d) < 7:
        return 0
    lookback = d.iloc[-10:-1] if len(d) >= 11 else d.iloc[:-1]
    count = 0
    for i in range(len(lookback) - 1, -1, -1):
        row = lookback.iloc[i]
        if not pd.isna(row["ma5"]) and row["close"] > row["ma5"]:
            count += 1
        else:
            break
    return count


def _is_first_touch_after_rally(d: pd.DataFrame, ma_name: str, rally_min: float = 0.15) -> tuple[bool, int]:
    """返回 (是否首次回踩, 回踩次数)."""
    if len(d) < 30:
        return False, 0
    ma_col = ma_name.lower().replace("ma", "ma")
    if ma_col not in d.columns:
        return False, 0

    recent = d.tail(30)
    highest_idx = recent["close"].idxmax()
    highest_pos = recent.index.get_loc(highest_idx)
    current_pos = len(recent) - 1

    if current_pos - highest_pos < 3:
        return False, 0

    peak_price = recent["close"].iloc[highest_pos]
    current_ma = recent[ma_col].iloc[-1]
    if pd.isna(current_ma):
        return False, 0

    rally_above_ma = (peak_price - current_ma) / current_ma
    if rally_above_ma < rally_min:
        return False, 0

    between = recent.iloc[highest_pos + 1:-1]
    if len(between) == 0:
        return True, 1
    prior_touches = int((abs(between["close"] - between[ma_col]) / between[ma_col] < 0.025).sum())
    touch_count = prior_touches + 1
    return prior_touches <= 1, touch_count


def _vol_ratio_to_recent_peak(d: pd.DataFrame) -> float:
    recent = d.tail(20)
    peak_vol = recent["volume"].max()
    if peak_vol == 0:
        return 1.0
    return d.iloc[-1]["volume"] / peak_vol


def _consolidation_days_near_ma(d: pd.DataFrame, ma_val: float, threshold: float = 0.03) -> int:
    count = 0
    for i in range(len(d) - 2, max(len(d) - 15, -1), -1):
        row = d.iloc[i]
        if abs(row["close"] - ma_val) / ma_val < threshold:
            count += 1
        else:
            break
    return count


def _consolidation_low_near_ma(d: pd.DataFrame, ma_val: float, threshold: float = 0.03) -> Optional[float]:
    consol_days = _consolidation_days_near_ma(d, ma_val, threshold)
    if consol_days < 2:
        return None
    consol_slice = d.tail(consol_days + 1).head(consol_days)
    return consol_slice["low"].min()


def _nearest_ma_label(close: float, row: pd.Series) -> str:
    """返回距离 close 最近的一根均线及偏离 %. 比较 MA5/10/20/60, 取 abs(偏离%) 最小."""
    candidates = []
    for label, key in (("MA5", "ma5"), ("MA10", "ma10"), ("MA20", "ma20"), ("MA60", "ma60")):
        val = row.get(key)
        if val is None or pd.isna(val) or val <= 0:
            continue
        dev = (close - float(val)) / float(val) * 100
        candidates.append((label, dev))
    if not candidates:
        return ""
    best = min(candidates, key=lambda x: abs(x[1]))
    return f"距{best[0]} {best[1]:+.2f}%"


def _intraday_after(earliest_minute: int, now=None) -> bool:
    """盘中早盘门槛:
    - 工作日 09:00-09:30 集合竞价: 视为盘中早时间, 走 earliest_minute 门槛
      (v1.7.106 修复: 此前 is_intraday()=False 误放行, 导致 S0/BUY_STRONG_START 在 09:25-09:30 被触发)
    - 连续竞价 09:30-11:30 / 13:00-15:00: 走门槛
    - 真盘后(午休/15:00+)/非交易日: 放行 True (供 EOD/回测路径)

    now: 测试注入用 (datetime). 不传则取 datetime.now().
    """
    from datetime import datetime as _dt
    _n = now if now is not None else _dt.now()
    cur_min = _n.hour * 60 + _n.minute
    if _n.weekday() < 5 and 9 * 60 <= cur_min < 9 * 60 + 30:
        return cur_min >= int(earliest_minute)
    if not is_intraday(_n):
        return True
    return cur_min >= int(earliest_minute)


# ── 检测器 ──

def _detect_s0_weak_extreme(d: pd.DataFrame, latest: pd.Series, sc: dict) -> Optional[str]:
    """检测"弱势极限": 6 条 AND 判定 (纯函数, 不含盘中时间门槛, 可回溯)."""
    if len(d) < 12:
        return None

    close = float(latest["close"])
    ma10 = float(latest["ma10"])
    ma20 = float(latest["ma20"])
    ma60 = float(latest["ma60"])
    vol_today = float(latest["volume"])

    win = int(sc.get("vol_floor_window", 10))
    vol_tol = float(sc.get("vol_floor_tolerance", 1.1))
    avg10_ratio = float(sc.get("vol_shrink_avg10_ratio", 0.75))
    ma10_above_max = float(sc.get("ma10_above_max_pct", 2.0)) / 100
    ma10_below_max = float(sc.get("ma10_below_max_pct", 2.0)) / 100
    ma20_above_max = float(sc.get("ma20_above_max_pct", 2.0)) / 100
    ma20_below_max = float(sc.get("ma20_below_max_pct", 2.0)) / 100

    vols_last_n = d.tail(win)["volume"]
    if len(vols_last_n) < win:
        return None
    min_vol_n = float(vols_last_n.min())
    avg_vol_n = float(vols_last_n.mean())
    if min_vol_n <= 0 or avg_vol_n <= 0:
        return None

    if not (vol_today <= min_vol_n * vol_tol):
        return None
    if not (vol_today <= avg_vol_n * avg10_ratio):
        return None
    if not (close > ma60):
        return None
    if not (close > ma20):
        return None
    ma10_ratio = (close - ma10) / ma10 if ma10 > 0 else 999
    ma20_ratio = (close - ma20) / ma20 if ma20 > 0 else 999
    near_ma10 = (-ma10_below_max <= ma10_ratio <= ma10_above_max)
    near_ma20 = (-ma20_below_max <= ma20_ratio <= ma20_above_max)
    if not (near_ma10 or near_ma20):
        return None

    if near_ma10 and near_ma20:
        hit_anchor = "MA10" if abs(ma10_ratio) <= abs(ma20_ratio) else "MA20"
    else:
        hit_anchor = "MA10" if near_ma10 else "MA20"

    # v1.7.77: 前置 — 必须是"强势主升浪的回踩"
    require_rally = bool(sc.get("require_prior_rally", True))
    peak_window = int(sc.get("rally_peak_within_bars", 15))
    rally_label = ""
    if require_rally:
        from backend.services.trading_concepts import detect_main_rally
        rally = detect_main_rally(d)
        if not rally.ever_qualified or rally.peak_idx is None:
            return None
        bars_since_peak = len(d) - 1 - rally.peak_idx
        if bars_since_peak > peak_window:
            return None
        rally_label = f"主升浪回踩(+{rally.peak_gain_pct:.0%}, 距峰{bars_since_peak}日) | "

    vol_vs_min = vol_today / min_vol_n
    vol_vs_avg = vol_today / avg_vol_n
    nearest = _nearest_ma_label(close, latest)

    return (
        f"{rally_label}"
        f"地量(近{win}日最低量的{vol_vs_min:.2f}倍 / 近{win}日均量的{vol_vs_avg:.2f}倍) | "
        f"锚点{hit_anchor} | {nearest} | "
        f"等待启动确认"
    )


def _detect_strong_start_right(d: pd.DataFrame, latest: pd.Series,
                                sc: dict, s0_sc: dict,
                                code: str | None = None, name: str = "") -> Optional[str]:
    """检测"强势起点（右侧）": 左侧弱势极限缩量基础上, 今日放量启动 (5 AND)."""
    if len(d) < 15:
        return None

    close = float(latest["close"])
    ma10 = float(latest["ma10"])
    ma20 = float(latest["ma20"])

    pct_today = float(latest.get("pct_change", 0) or 0)
    min_pct = float(sc.get("min_pct_change", 2.0)) / 100
    if pct_today < min_pct:
        return None
    if not (close > ma10 or close > ma20):
        return None

    # v1.7.529: 排除"触发侧追涨停" (同 BUY_VOL_BREAKOUT v1.7.520) — 现价已封/逼近今日涨停板时不发买点。
    #   强势起点本就是"今日已放量涨起来"才认定的右侧追入, 09:31 报出时常已秒拉至涨停板上, 用户根本挂不进
    #   (实例: 多氟多 0629 09:31 报出即买不到) → 现价距板 ≤ chase_limit_buffer_pct 视为接近涨停不报。
    #   板幅感知(主板10%/创业科创20%/北交所30%/ST5%); 回测无 code(realtime=None) → is_at_limit_up 返回
    #   False 自动跳过, 与历史回测口径一致。
    if bool(sc.get("chase_limit_skip", True)) and code:
        prev_close = float(d.iloc[-2].get("close") or 0)
        if prev_close > 0:
            cur_pct = (close - prev_close) / prev_close * 100
            if is_at_limit_up(code, cur_pct, name, tol=float(sc.get("chase_limit_buffer_pct", 1.0))):
                return None

    lookback = int(sc.get("lookback_days", 5))
    qualified_day_idx = None
    baseline_vol = None
    baseline_close = None
    for offset in range(2, min(lookback + 2, len(d) + 1)):
        day_idx = len(d) - offset
        if day_idx < 12:
            break
        d_slice = d.iloc[:day_idx + 1]
        day_row = d.iloc[day_idx]
        if _detect_s0_weak_extreme(d_slice, day_row, s0_sc) is not None:
            qualified_day_idx = day_idx
            baseline_vol = float(day_row["volume"])
            baseline_close = float(day_row["close"])
            break
    if qualified_day_idx is None or baseline_vol is None or baseline_vol <= 0:
        return None

    # v1.7.420: 距基准涨幅上限 — 触发日现价相对"弱势极限那天收盘"涨幅 > 阈值 则不报。
    #   lookback_days=5 偏松: 弱势极限可能在很多天前, 中间已一字/缩量拉走(放量当天报不出),
    #   直到第5天才凑齐放量+涨幅条件 → 此时股价已大涨(如圣泉0608弱势极限→0615已+29%), 是
    #   "晚到的二/三浪追高", 非真"起点"。用现价距基准涨幅封顶挡住已大涨的追入。
    #   max_gain_from_base_pct<=0 视为关闭。
    gain_from_base = None
    if baseline_close and baseline_close > 0:
        gain_from_base = (close - baseline_close) / baseline_close
        max_gain = float(sc.get("max_gain_from_base_pct", 0)) / 100
        if max_gain > 0 and gain_from_base > max_gain:
            return None

    today_vol = float(latest["volume"])
    vol_mult_req = float(sc.get("vol_multiplier", 3.0))
    vol_mult_actual = today_vol / baseline_vol
    if vol_mult_actual < vol_mult_req:
        return None

    # v1.7.179: 绝对量门槛 — 今日量须 ≥ 近N日均量 × k. baseline 是左侧"地量",
    #   地量×3 在绝对量上可能仍很小, 此门槛挡住"地量×3 但绝对量不足"的伪启动.
    #   min_vol_vs_avgN<=0 视为关闭. 均量取今日之前的 N 个交易日(不含今日, 避免今日放量自抬基准).
    avg_window = int(sc.get("vol_avg_window", 10))
    min_vs_avg = float(sc.get("min_vol_vs_avgN", 1.5))
    vol_vs_avg = None
    if min_vs_avg > 0 and len(d) > avg_window:
        prior_vols = d["volume"].iloc[-(avg_window + 1):-1]
        avg_vol_n = float(prior_vols.mean())
        if avg_vol_n > 0:
            vol_vs_avg = today_vol / avg_vol_n
            if vol_vs_avg < min_vs_avg:
                return None

    est_amount = float(latest.get("amount_est", 0) or 0)
    min_amount = float(sc.get("min_full_day_amount", 2_000_000_000))
    if est_amount < min_amount:
        return None

    # v1.7.417: 实时累计成交额下限 (amount_now, 未外推) — 配合去掉10:00时间门槛。
    #   早盘外推系数极小(9:31≈0.009, 等于拿1分钟的量×100倒推全天), 量/额(amount_est)门槛形同虚设;
    #   改用"真实已成交额 ≥ 阈值"作硬闸门替代时间限制, 挡住开盘前几分钟的过度外推伪起爆。
    #   回测无 amount_now → 回退 amount_est(全天额), 必然 ≥ 阈值, 等价不额外约束 (回测口径不变)。
    min_amt_now = float(sc.get("min_amount_now", 0))
    if min_amt_now > 0:
        amt_now = float(latest.get("amount_now", latest.get("amount_est", 0)) or 0)
        if amt_now < min_amt_now:
            return None

    above = []
    if close > ma10:
        above.append(f"MA10({(close - ma10) / ma10 * 100:+.2f}%)")
    if close > ma20:
        above.append(f"MA20({(close - ma20) / ma20 * 100:+.2f}%)")

    days_ago = len(d) - 1 - qualified_day_idx
    avg_label = f"(近{avg_window}日均量的{vol_vs_avg:.1f}倍) " if vol_vs_avg is not None else ""
    base_label = f"(距基准{gain_from_base * 100:+.1f}%) " if gain_from_base is not None else ""
    return (
        f"前{days_ago}日弱势极限(量{baseline_vol:.0f}){base_label}→ "
        f"今日预估量{vol_mult_actual:.1f}x地量 {avg_label}| "
        f"当前成交额{est_amount / 1e8:.2f}亿 | "
        f"涨{pct_today * 100:.2f}% 站上 {' & '.join(above) if above else 'N/A'}"
    )


def _detect_rally_ma20_pullback(d: pd.DataFrame, latest: pd.Series, sc: dict) -> Optional[str]:
    """检测"主升浪回踩20MA缩量后突破昨高·缩量后突破昨高"买点 (右侧, 与弱势极限地量缩量互补):
      [昨日 setup]
        ① 主升浪前置: 前有 ≥15% 主升浪, 峰值距今(到昨日) ≤ rally_peak_within_bars 交易日
        ② 回踩20日线: 昨日 close 距 MA20 在 ±ma20_touch_pct% 内
        ③ 回踩日缩量: 昨日量 < 近10日均量 × shrink_ratio (卖盘衰竭, 质量关键)
        ④ 流动性: 近10日均成交额(量×收盘 近似) > min_avg10_amount
      [今日 trigger]
        ⑤ 盘中突破: 今日最高 > 昨日最高 × (1 + breakout_pct%)  → 买点(过滤假突破)

    专抓"强势缩量回踩中期线、次日放量突破"——弱势极限抓不到的急跌/高量回踩(如多氟多)。
    回测(自选股近1年): 触发48 胜率46% 胜负比1.9:1 平均+6.9%(T+5)。
    """
    if len(d) < 26:
        return None
    prev = d.iloc[-2]   # 昨日 = 回踩 setup 候选
    prev_high = float(prev.get("high") or 0)
    if prev_high <= 0:
        return None

    # ⑤ 今日盘中突破昨高 ×(1+breakout) — latest['high'] 实时=盘中最高, 回测=全天最高
    breakout = float(sc.get("breakout_pct", 2.5)) / 100
    trigger_lvl = prev_high * (1 + breakout)
    if float(latest.get("high") or 0) < trigger_lvl:
        return None

    # ② 昨日回踩锚点均线 (默认MA20; 可配 touch_ma="ma10" 派生"回踩10MA缩量后突破昨高")
    ma_col = sc.get("touch_ma", "ma20")
    pma20 = float(prev.get(ma_col) or 0)
    if pd.isna(pma20) or pma20 <= 0:
        return None
    touch = float(sc.get("ma20_touch_pct", 3.0)) / 100
    if not (-touch <= (float(prev["close"]) - pma20) / pma20 <= touch):
        return None

    # ③ 昨日缩量 (近10日均量, 截至昨日)
    win = int(sc.get("amount_avg_window", 10))
    prev_pos = len(d) - 2
    vol_seg = d["volume"].iloc[prev_pos - win + 1: prev_pos + 1]
    if len(vol_seg) < win:
        return None
    avg10v = float(vol_seg.mean())
    if avg10v <= 0 or float(prev["volume"]) >= avg10v * float(sc.get("shrink_ratio", 0.8)):
        return None

    # ④ 流动性闸门 (v1.7.x)
    #   - min_full_day_amount: 累计成交额底线 (实盘=amount_now实时累计未外推, 回测=amount_est全天额)
    #   - vol_mult_avg10>0 时再叠一道"放量确认"双闸 (v1.7.462 回踩MA10): 当日量 ≥ 近10日均量 × 倍数,
    #     替代"干等成交额堆够10亿"——小盘急拉票突破即报、不再追在高位。当日量 latest['volume'] 盘中已被
    #     signal_engine 用U型系数外推成全天预估量, 回测=全天实际量, 二者口径一致(同 bt_rally10_gate 验证)。
    #   回测无 amount_now → 回退 amount_est(=量×收盘的全天额), 回测口径不变。
    est_amount = float(latest.get("amount_now", latest.get("amount_est", 0)) or 0)
    if est_amount < float(sc.get("min_full_day_amount", 1_000_000_000)):
        return None
    vol_mult = float(sc.get("vol_mult_avg10", 0) or 0)
    if vol_mult > 0 and (avg10v <= 0 or float(latest.get("volume") or 0) < avg10v * vol_mult):
        return None

    # ① 主升浪前置 — 与弱势极限同口径(v1.7.213): detect_main_rally 截至今日(非昨日),
    #   峰值距"今日"≤rally_peak_within_bars。此前锚定昨日(prev_pos), 与弱势极限差1日且
    #   回溯偏移不一致, 现统一为 len(d)-1-peak_idx <= 30。
    rally_label = ""
    if bool(sc.get("require_prior_rally", True)):
        from backend.services.trading_concepts import detect_main_rally
        rally = detect_main_rally(d)
        if not rally.ever_qualified or rally.peak_idx is None:
            return None
        bars = len(d) - 1 - rally.peak_idx
        if bars > int(sc.get("rally_peak_within_bars", 30)):
            return None
        rally_label = f"主升浪(+{rally.peak_gain_pct:.0%},距峰{bars}日) → "

    _anchor_lbl = "MA10" if sc.get("touch_ma", "ma20") == "ma10" else "MA20"
    vol_tag = ""
    if vol_mult > 0:
        vol_tag = f" | 今日放量{float(latest.get('volume') or 0) / avg10v:.2f}倍(近10日均量)"
    return (f"{rally_label}缩量回踩{_anchor_lbl}(昨量是均量的{float(prev['volume']) / avg10v:.2f}倍) "
            f"→ 突破昨高{breakout * 100:.1f}%(触发价{trigger_lvl:.2f}) | "
            f"实时成交额{est_amount / 1e8:.1f}亿{vol_tag}")


def _detect_vol_breakout(d: pd.DataFrame, latest: pd.Series, sc: dict,
                         code: str | None = None, name: str = "") -> Optional[str]:
    """检测"缩量突破昨高"买点 (右侧, 不锚定均线、不要主升浪前置):
      [昨日 setup] 缩量整理: 昨量 < 近10日均量(截至昨日) × shrink_ratio
      [今日 trigger]
        ① 放量:   今日量 ≥ 昨量 × vol_mult_prev  且  ≥ 近10日均量 × vol_mult_avg10
        ② 突破:   今日最高 ≥ 昨高 × (1 + breakout_pct%)
        ③ 站位:   今日收盘站上 MA10 或 MA20
        ④ 流动性: 外推全天额 amount_est ≥ min_full_day_amount(默认10亿) 且 实时累计额 amount_now ≥ min_amount_now(默认5亿)

    全市场半年双段回测(入场出场均网格寻优): 胜率65% 均值+3.1% 盈利因子2.24(样本内2.19/外2.29)。
    本质=1日微型平台突破, 与回踩同源但不锚均线、不要主升浪, 故触发更广。
    """
    if len(d) < 12:
        return None
    prev = d.iloc[-2]
    prev_vol = float(prev.get("volume") or 0)
    prev_high = float(prev.get("high") or 0)
    if prev_vol <= 0 or prev_high <= 0:
        return None

    # 近10日均量(截至昨日, 不含今日): 末 11 根去掉今日 = 10 根截至 prev
    avg10_y = float(d["volume"].iloc[-11:-1].mean())
    if avg10_y <= 0:
        return None

    # 昨日缩量整理
    if not (prev_vol < avg10_y * float(sc.get("shrink_ratio", 0.8))):
        return None

    # 排除"封板假缩量" (v1.7.519): 缩量设置日若是涨停封板, 低换手是封死所致而非整理蓄势,
    # 次日突破=涨停后加速段(非休整后启动), 此类实测是减分项(同向 ZT-filter 回测)。
    # 检测器无 code 不便取精确板幅, 用板无关近似: 昨日收盘≈最高(封死) 且 昨涨幅 ≥ 阈值
    # (默认9.5%, 覆盖主板10%/创业科创20%/北交所30%封板; ST 5%封板漏掉可接受)。zt_setup_skip 关。
    if bool(sc.get("zt_setup_skip", True)) and len(d) >= 3:
        pp_close = float(d.iloc[-3].get("close") or 0)
        if pp_close > 0:
            prev_pct = (float(prev["close"]) - pp_close) / pp_close * 100
            sealed = float(prev["close"]) >= prev_high * 0.999
            if sealed and prev_pct >= float(sc.get("zt_setup_pct_min", 9.5)):
                return None

    # 排除"触发侧追涨停" (v1.7.520): 现价已封/逼近今日涨停板时不发买点。
    #   缩量突破靠"今日最高突破昨高2%"确认, 个股一旦冲到涨停, 最高远超触发价、现价就贴在板上,
    #   此时入场=追板/炸板高位接盘(实例: 洪田股份603800 06-25 09:44冲涨停84.95=昨收×1.10被误发)。
    #   板幅感知(主板10%/创业科创20%/北交所30%/ST5%): 现价距板 ≤ chase_limit_buffer_pct 视为接近涨停。
    #   回测无 code(realtime=None)→ is_at_limit_up 返回 False 自动跳过, 与历史回测口径一致。
    if bool(sc.get("chase_limit_skip", True)) and code:
        prev_close = float(prev.get("close") or 0)
        if prev_close > 0:
            cur_pct = (float(latest["close"]) - prev_close) / prev_close * 100
            if is_at_limit_up(code, cur_pct, name, tol=float(sc.get("chase_limit_buffer_pct", 1.0))):
                return None

    # 缩量日长下影线(可选): 缩量整理日盘中被砸下去又被买回 = 下方有承接/反弹需求, 次日放量突破更可靠。
    #   半年全市场回测: 单加此项(下影≥0.4) PF2.33→2.87、胜率66→70%、样本内外均70%(稳健, 样本约-75%但够用)。
    shadow_label = ""
    if bool(sc.get("REQ_PREV_SHADOW", False)):
        p_low = float(prev.get("low") or 0)
        p_open = float(prev.get("open") or 0)
        p_close = float(prev["close"])
        rng = prev_high - p_low
        if rng <= 0:
            return None
        frac = (min(p_open, p_close) - p_low) / rng
        if frac < float(sc.get("PREV_SHADOW_MIN", 0.4)):
            return None
        shadow_label = f"缩量日下影{frac * 100:.0f}% "

    # 今日放量
    today_vol = float(latest["volume"])
    if today_vol < prev_vol * float(sc.get("vol_mult_prev", 2.0)):
        return None
    if today_vol < avg10_y * float(sc.get("vol_mult_avg10", 1.5)):
        return None

    # 突破昨高
    breakout = float(sc.get("breakout_pct", 2.0)) / 100
    trigger_lvl = prev_high * (1 + breakout)
    if float(latest.get("high") or 0) < trigger_lvl:
        return None

    # 站上均线
    close = float(latest["close"])
    ma10 = float(latest.get("ma10") or 0)
    ma20 = float(latest.get("ma20") or 0)
    above = []
    if ma10 > 0 and close > ma10:
        above.append("MA10")
    if ma20 > 0 and close > ma20:
        above.append("MA20")
    if not above:
        return None

    # 流动性双闸 (v1.7.428, 配合去掉10:00时间门槛):
    #   ① 外推全天额 amount_est ≥ min_full_day_amount(默认10亿; v1.7.430回测确认10亿优于20亿)
    #   ② 实时累计额 amount_now ≥ min_amount_now(默认5亿, 未外推): 早盘外推系数极小(9:31≈0.009,
    #      拿1分钟量×100倒推全天)使①形同虚设, 用真实已成交额作硬闸门挡开盘前几分钟过度外推伪突破。
    #   回测无 amount_now → 回退 amount_est(≥①阈值必≥②), 等价不额外约束(回测口径仅受①约束)。
    est_amount = float(latest.get("amount_est", 0) or 0)
    if est_amount < float(sc.get("min_full_day_amount", 1_000_000_000)):
        return None
    min_amt_now = float(sc.get("min_amount_now", 0))
    if min_amt_now > 0:
        amt_now = float(latest.get("amount_now", latest.get("amount_est", 0)) or 0)
        if amt_now < min_amt_now:
            return None

    return (f"昨缩量(近10日均量的{prev_vol / avg10_y:.2f}倍) {shadow_label}→ "
            f"今日放量(均量的{today_vol / avg10_y:.1f}倍、昨量的{today_vol / prev_vol:.1f}倍) "
            f"突破昨高{breakout * 100:.1f}%(触发价{trigger_lvl:.2f}) | "
            f"站上{'&'.join(above)} | 当前成交额{est_amount / 1e8:.1f}亿")


def _detect_platform_breakout(d: pd.DataFrame, latest: pd.Series, sc: dict) -> Optional[str]:
    """检测"中继平台突破"买点 (右侧, 多日横盘窄平台后收盘突破上沿):
      [平台 setup] 今日之前 L 根横盘:
        ① 平台上沿 PH = 平台窗最高价; 收盘振幅 (maxClose-minClose)/minClose ≤ A (窄平台)
        ② 中继前置(可选): 平台前 N_PRIOR 日内有主升 (PH-priorLow)/priorLow ≥ R
        ③ 不破位(可选): 平台期收盘不深破 MA20 (close ≥ ma20×0.95)
      [今日 trigger]
        ④ 收盘确认突破: 今收(MODE=close)或最高(MODE=high) ≥ PH×(1+BUF)
        ⑤ 放量(可选): 今量 ≥ 平台均量 × V
        ⑥ 流动性: 今日当前成交额 ≥ min_full_day_amount

    全市场半年回测(出场同回踩10MA缩量后突破昨高): 主配置 胜率69% 均值+4.3% 盈利因子3.08(样本外2.85/2026切片3.02), 完胜现有买点。
    两条硬约束: 收盘确认必须(改盘中口径 PF 塌到1.72, 故配尾盘门槛); 顺势动量、退潮/分化月走平(靠引擎层 regime 闸门降级停发)。
    与缩量突破(本质=1日微型平台)的边界: 本模型是多日横盘平台整理后突破上沿。
    """
    L = int(sc.get("L", 8))
    NP = int(sc.get("N_PRIOR", 20))
    req_prior = bool(sc.get("REQ_PRIOR", True))
    if len(d) < L + 1 + (NP if req_prior else 0):
        return None

    plat = d.iloc[-(L + 1):-1]            # 平台窗 = 今日之前 L 根(不含今日)
    PH = float(plat["high"].max())
    ch = float(plat["close"].max()); cl = float(plat["close"].min())
    if cl <= 0 or PH <= 0:
        return None
    amp = (ch - cl) / cl
    if amp > float(sc.get("A", 0.15)):    # 平台收盘振幅(窄)
        return None

    # ②a 中位数小幅上行(可选): 平台后半收盘中位 / 前半中位 ∈ [RISE_MIN, RISE_MAX]
    #     只留"缓慢抬高的台阶", 剔走平/下倾/陡冲。半年全市场回测: 单加此项 PF3.29→3.34、样本外胜率69→71%(更稳)。
    rise_label = ""
    if bool(sc.get("REQ_RISE", False)):
        half = L // 2
        if half >= 1:
            med1 = float(plat["close"].iloc[:half].median())
            med2 = float(plat["close"].iloc[half:].median())
            if med1 <= 0:
                return None
            rise = med2 / med1 - 1.0
            if rise < float(sc.get("RISE_MIN", 0.0)) or rise > float(sc.get("RISE_MAX", 0.05)):
                return None
            rise_label = f"缓升{rise * 100:.1f}% "

    # ② 中继前置(可选)
    prior_label = ""
    if req_prior:
        prior = d.iloc[-(L + 1 + NP):-(L + 1)]
        prior_low = float(prior["low"].min()) if len(prior) else 0.0
        if prior_low <= 0:
            return None
        prior_gain = (PH - prior_low) / prior_low
        if prior_gain < float(sc.get("R", 0.20)):
            return None
        prior_label = f"前置主升+{prior_gain:.0%} → "

    # ③ 不破位(可选)
    if bool(sc.get("REQ_HOLD", False)):
        m = plat["ma20"]
        if not m.isna().any() and not (plat["close"] >= m * 0.95).all():
            return None

    # ④ 收盘确认突破上沿
    buf = float(sc.get("BUF", 0.005))
    lvl = PH * (1 + buf)
    px = float(latest["close"]) if sc.get("MODE", "close") == "close" else float(latest.get("high") or 0)
    if px < lvl:
        return None

    # ⑤ 放量(可选)
    vol_label = ""
    if bool(sc.get("REQ_VOL", True)):
        pav = float(plat["volume"].mean())
        today_vol = float(latest["volume"])
        if pav <= 0 or today_vol < pav * float(sc.get("V", 1.3)):
            return None
        vol_label = f"放量{today_vol / pav:.1f}x平台均 "

    # ⑥ 流动性
    est_amount = float(latest.get("amount_est", 0) or 0)
    if est_amount < float(sc.get("min_full_day_amount", 1_000_000_000)):
        return None

    return (f"{prior_label}中继平台{L}日(振幅{amp * 100:.1f}%, {rise_label}上沿{PH:.2f}) → "
            f"收盘突破+{buf * 100:.1f}%(触发价{lvl:.2f}) {vol_label}| "
            f"当前成交额{est_amount / 1e8:.1f}亿")


def _detect_auction_strength(d: pd.DataFrame, latest: pd.Series, sc: dict) -> Optional[str]:
    """检测"竞价高开弱转强"买点 (v1.7.275, 竞价9:26触发, 不含情绪/竞价额门控 — 那两道在引擎接线层做):
      [昨日 setup]
        ① 多头排列: 昨收 > MA20 > MA60
        ② 主升浪:   近20日涨幅 ≥ rally_min_pct% (截至昨日)
        ③ 缩量小回调: 昨量 ≤ 近10日均量 × shrink_ratio  且  昨涨幅 ∈ [min_t1_ret, max_t1_ret]  且  昨收 > MA10
      [今日 trigger]
        ④ 竞价高开: 今开 / 昨收 - 1 ≥ gap_min_pct%   (上限 gap_max_pct% 排除一字板/不可买)

    源自 bt_wts_compare.py 的"强势弱转强 S"定义; 入场=次日竞价开盘价。
    """
    if len(d) < 22:
        return None
    prev = d.iloc[-2]                      # 昨日 = setup
    pclose = float(prev["close"])
    pma10 = prev.get("ma10", np.nan)
    pma20 = prev.get("ma20", np.nan)
    pma60 = prev.get("ma60", np.nan)
    if pd.isna(pma10) or pd.isna(pma20) or pd.isna(pma60) or pclose <= 0:
        return None

    # ① 多头排列
    if not (pclose > pma20 > pma60):
        return None
    # ② 近20日主升浪 (截至昨日)
    c20 = float(d.iloc[-22]["close"])
    rally = pclose / c20 - 1.0 if c20 > 0 else 0.0
    if rally < float(sc.get("rally_min_pct", 15.0)) / 100:
        return None
    # ③ 昨日缩量小回调
    win = int(sc.get("amount_avg_window", 10))
    vol_seg = d["volume"].iloc[-(win + 1):-1]   # 近 win 日, 截至昨日
    if len(vol_seg) < win:
        return None
    avg_vol = float(vol_seg.mean())
    if avg_vol <= 0 or float(prev["volume"]) > avg_vol * float(sc.get("shrink_ratio", 0.8)):
        return None
    pprev_close = float(d.iloc[-3]["close"])
    t1_ret = pclose / pprev_close - 1.0 if pprev_close > 0 else 0.0
    if not (float(sc.get("min_t1_ret", -5.0)) / 100 <= t1_ret <= float(sc.get("max_t1_ret", 1.0)) / 100):
        return None
    if not (pclose > pma10):
        return None
    # ④ 今日竞价高开
    open_t = float(latest.get("open") or 0)
    gap = open_t / pclose - 1.0 if open_t > 0 else -1.0
    gap_min = float(sc.get("gap_min_pct", 3.0)) / 100
    gap_max = float(sc.get("gap_max_pct", 9.0)) / 100
    if gap < gap_min or gap > gap_max:
        return None

    return (f"主升浪(近20日+{rally:.0%})缩量小回调(昨量是均量的{float(prev['volume']) / avg_vol:.2f}倍, "
            f"昨{t1_ret:+.1%}) → 今竞价高开{gap:.1%} | 昨收站上MA20>MA60")


def _detect_s3_rally_pullback(d: pd.DataFrame, latest: pd.Series, sc: dict) -> Optional[str]:
    """检测 S3 买点: 主升浪后回踩 MA10 企稳. v1.7.90 已下线,仅供策略回测页历史分析."""
    close = latest["close"]
    ma5 = latest["ma5"]
    ma10 = latest["ma10"]
    ma20 = latest["ma20"]

    if not (ma5 > ma10 > ma20):
        return None

    touch_pct = sc.get("ma10_touch_pct", 2.0) / 100
    dist_to_ma10 = (close - ma10) / ma10
    if dist_to_ma10 > touch_pct or dist_to_ma10 < -touch_pct:
        return None

    lookback = min(30, len(d))
    recent = d.tail(lookback)
    high_idx = recent["high"].idxmax()
    high_pos = recent.index.get_loc(high_idx)
    recent_high = recent["high"].iloc[high_pos]
    if high_pos >= len(recent) - 2:
        return None

    rally_start_price = None
    rally_start_pos = None
    for i in range(high_pos - 1, -1, -1):
        row = recent.iloc[i]
        if pd.isna(row.get("ma10", np.nan)):
            continue
        if row["close"] < row["ma10"]:
            for j in range(i + 1, high_pos + 1):
                cross_row = recent.iloc[j]
                if not pd.isna(cross_row.get("ma10", np.nan)) and cross_row["close"] > cross_row["ma10"]:
                    rally_start_price = cross_row["low"]
                    rally_start_pos = j
                    break
            break

    if rally_start_price is None:
        return None

    rally_min = sc.get("rally_min_pct", 15.0) / 100
    rally_pct = (recent_high - rally_start_price) / rally_start_price
    if rally_pct < rally_min:
        return None

    pullback_min = sc.get("pullback_min_pct", 5.0) / 100
    pullback_max = sc.get("pullback_max_pct", 15.0) / 100
    pullback_pct = (recent_high - close) / recent_high
    if pullback_pct < pullback_min or pullback_pct > pullback_max:
        return None

    vol_shrink_threshold = sc.get("vol_shrink_threshold", 0.8)
    vol_ratio_20 = latest.get("vol_ratio_20", np.nan)
    if not pd.isna(vol_ratio_20) and vol_ratio_20 > vol_shrink_threshold:
        return None

    if len(d) >= 3:
        hist_today = latest.get("macd_hist", np.nan)
        hist_prev = d.iloc[-2].get("macd_hist", np.nan)
        hist_prev2 = d.iloc[-3].get("macd_hist", np.nan)
        if not any(pd.isna(v) for v in [hist_today, hist_prev, hist_prev2]):
            change_today = hist_today - hist_prev
            change_prev = hist_prev - hist_prev2
            if change_today <= change_prev:
                return None

    ma10_tolerance = sc.get("ma10_break_tolerance", 3.0) / 100
    recent_3 = d.tail(3)
    for _, row in recent_3.iterrows():
        row_ma10 = row.get("ma10", np.nan)
        if not pd.isna(row_ma10) and row["low"] < row_ma10 * (1 - ma10_tolerance):
            return None

    ma5_recover_pct = sc.get("ma5_recover_pct", 99.8) / 100
    if pd.isna(ma5) or close < ma5 * ma5_recover_pct:
        return None

    pct_change_min = sc.get("pct_change_min", 1.5) / 100
    pct_today = latest.get("pct_change", 0)
    if pd.isna(pct_today) or pct_today < pct_change_min:
        return None

    between = recent.iloc[high_pos + 1:]
    touch_count = int((abs(between["close"] - between["ma10"]) / between["ma10"] < touch_pct).sum())
    max_touch = sc.get("max_touch_count", 2)
    if touch_count > max_touch:
        return None

    touch_label = "首次触碰" if touch_count <= 1 else f"第{touch_count}次触碰"
    return (
        f"主升浪涨幅{rally_pct:.1%}后回踩10日线 | "
        f"{touch_label} | 回撤{pullback_pct:.1%} | 收复MA5+涨{pct_today:.1%}"
    )


def get_stock_ma_status(d: pd.DataFrame) -> dict:
    """给一只票打 MA 位置标签 (强势/震荡/偏弱/极弱)."""
    from backend.services.signal_engine_indicators import compute_indicators
    if len(d) < 5:
        return {}
    ind = compute_indicators(d)
    latest = ind.iloc[-1]
    close = latest["close"]

    status = {"close": close}
    for ma_name in ["ma5", "ma10", "ma20", "ma60"]:
        val = latest.get(ma_name, np.nan)
        if pd.isna(val):
            status[ma_name] = None
            status[f"{ma_name}_pct"] = None
        else:
            status[ma_name] = round(val, 2)
            status[f"{ma_name}_pct"] = round((close - val) / val * 100, 2)

    if status.get("ma5") and close > status["ma5"]:
        if status.get("ma10") and close > status["ma10"]:
            status["position"] = "强势（MA5+MA10上方）"
        else:
            status["position"] = "偏强（MA5上方）"
    elif status.get("ma10") and close > status["ma10"]:
        status["position"] = "震荡（MA5下方MA10上方）"
    elif status.get("ma20") and close > status["ma20"]:
        status["position"] = "偏弱（MA10下方MA20上方）"
    elif status.get("ma60") and close > status["ma60"]:
        status["position"] = "弱势（MA20下方MA60上方）"
    else:
        status["position"] = "极弱（MA60下方）"

    return status
