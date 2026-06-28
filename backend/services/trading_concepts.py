"""
公共交易概念量化定义模块

每个概念对应一组阈值常量 + 判定函数。所有信号模型(S1/S2/M1/M2 等)、
sector_leader、backtest 都应引用本模块,确保整套系统口径一致。

已锁定的概念:
  1. 主升浪 (MainRally)      → detect_main_rally()
  7. 真跌破 (RealBreak)       → is_real_break() / is_false_break()

待对齐(按 核心交易概念量化标准.md 的顺序):
  2. 弱势极限   3. 强势起点   4. 缩量      5. 爆量
  6. 资金回流   8. 资金认可   9. 主流题材  10. 真受益
  11. 单笔风控
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ════════════════════════════════════════════════════════════
# 1. 主升浪 (Main Rally) ✅ 已锁定(v3)
#
# 定义:
#   起点 = close 从 MA10 下方上穿 MA10 当日的 close
#         (且上穿当日成交量 ≥ 5日均量 × 1.2)
#
#   曾经发生过(ever_qualified):
#         起点后 30 日窗口内,峰值涨幅 ≥ 15%
#
#   当前仍在(in_rally):
#         ever_qualified 为 True
#         AND 当前从峰值回撤 ≤ 8%
#         AND 窗口内未出现"连续两日收盘 < MA10"
#
#   结束信号(对应文档第9课"真跌破"):
#         窗口内任意 T 日 close < MA10  AND  T+1 日 close < MA10
#         → 主升浪结束(in_rally = False)
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MainRallyConfig:
    lookback_n: int = 30                  # 起点之后追踪窗口(交易日)
    breakout_vol_mult: float = 1.5        # 上穿当日量能放大倍数
    min_gain_pct: float = 0.15            # 涨幅门槛 15%
    max_drawdown_pct: float = 0.08        # 回撤上限 8%
    search_lookback_mult: int = 2         # 倒序搜索入口的范围 = lookback_n × 此倍数


MAIN_RALLY_CFG = MainRallyConfig()


def main_rally_config_from_dict(cfg: dict | None) -> MainRallyConfig:
    """从配置字典(如 signal_engine.DEFAULT_SIGNAL_CONFIG['MAIN_RALLY'])构建。

    支持 min_gain_pct / max_drawdown_pct 以"百分数"形式传入(如 15.0 = 15%)。
    """
    if not cfg:
        return MAIN_RALLY_CFG

    def _pct(v, default: float) -> float:
        if v is None:
            return default
        v = float(v)
        return v / 100.0 if v > 1.0 else v

    return MainRallyConfig(
        lookback_n=int(cfg.get("lookback_n", MAIN_RALLY_CFG.lookback_n)),
        breakout_vol_mult=float(cfg.get("breakout_vol_mult", MAIN_RALLY_CFG.breakout_vol_mult)),
        min_gain_pct=_pct(cfg.get("min_gain_pct"), MAIN_RALLY_CFG.min_gain_pct),
        max_drawdown_pct=_pct(cfg.get("max_drawdown_pct"), MAIN_RALLY_CFG.max_drawdown_pct),
        search_lookback_mult=int(cfg.get("search_lookback_mult", MAIN_RALLY_CFG.search_lookback_mult)),
    )


@dataclass
class MainRallyState:
    """单次主升浪的识别结果。"""
    in_rally: bool                                  # 当前是否仍处于主升浪
    ever_qualified: bool = False                    # 起点到当前窗口内,是否曾达成过主升浪条件
    start_idx: Optional[int] = None                 # 起点 K 线索引(在传入 df 中)
    start_date: Optional[str] = None
    start_close: Optional[float] = None
    peak_idx: Optional[int] = None                  # 期间最高 K 线索引
    peak_high: Optional[float] = None
    peak_gain_pct: float = 0.0                      # 起点到峰值的涨幅(== current_gain_pct)
    current_gain_pct: float = 0.0                   # 起点到期间峰值的涨幅(保留兼容)
    current_drawdown_pct: float = 0.0               # 从峰值的回撤
    bars_since_start: int = 0
    end_reason: Optional[str] = None                # 已结束的原因(若不在主升浪中)


def _is_breakout_bar(df: pd.DataFrame, i: int, cfg: MainRallyConfig) -> bool:
    """判断 df.iloc[i] 是否为「放量上穿 MA10」的入口 K 线。"""
    if i < 1:
        return False
    if 'ma10' not in df.columns or 'vol_ma5' not in df.columns:
        return False

    today = df.iloc[i]
    yesterday = df.iloc[i - 1]

    if pd.isna(yesterday['ma10']) or pd.isna(today['ma10']):
        return False
    if pd.isna(today['vol_ma5']) or today['vol_ma5'] == 0:
        return False

    cross_up = yesterday['close'] < yesterday['ma10'] and today['close'] >= today['ma10']
    vol_break = today['volume'] >= today['vol_ma5'] * cfg.breakout_vol_mult
    return cross_up and vol_break


def detect_main_rally(df: pd.DataFrame,
                      cfg: MainRallyConfig = MAIN_RALLY_CFG) -> MainRallyState:
    """识别当前是否处于一段主升浪中。

    扫描最近 lookback_n × search_lookback_mult 个交易日,倒序找最近一次
    「放量上穿 MA10」入口,然后从入口向后追踪到当前 K 线,判断:
      1. 起点到峰值涨幅是否 ≥ min_gain_pct
      2. 当前价相对峰值回撤是否 ≤ max_drawdown_pct
      3. 期间收盘价是否再次跌破 MA10

    Args:
        df: 必须包含 close, high, low, volume, ma10, vol_ma5 列。
            通常由 signal_engine.compute_indicators(df) 生成。

    Returns:
        MainRallyState - 当前主升浪状态。
        in_rally=True 表示当前满足主升浪三条件。
    """
    if len(df) < 12:                                # 至少需要 MA10 + 上穿前1日
        return MainRallyState(in_rally=False, end_reason="数据不足")
    if 'ma10' not in df.columns or 'vol_ma5' not in df.columns:
        return MainRallyState(in_rally=False, end_reason="缺少指标列")

    search_end = len(df) - 1
    search_start = max(0, search_end - cfg.lookback_n * cfg.search_lookback_mult)

    # 收集所有"放量上穿 MA10"事件,倒序找最近一个之后 30 日内涨幅达标的
    upcross_candidates = [i for i in range(search_end, search_start, -1)
                          if _is_breakout_bar(df, i, cfg)]

    if not upcross_candidates:
        return MainRallyState(in_rally=False, end_reason="未找到放量上穿入口")

    # 倒序遍历:选最近一个"30 日内峰值涨幅达标"的上穿作为主升浪起点
    breakout_idx: Optional[int] = None
    for idx in upcross_candidates:
        win_end = min(idx + cfg.lookback_n, len(df) - 1)
        after = df.iloc[idx + 1:win_end + 1]
        if len(after) == 0:
            continue
        peak = float(after['high'].max())
        sc = float(df.iloc[idx]['close'])
        if (peak - sc) / sc >= cfg.min_gain_pct:
            breakout_idx = idx
            break

    # 若都没达标,用最近一个作为代表(给出"涨幅不足"原因)
    if breakout_idx is None:
        breakout_idx = upcross_candidates[0]

    # 起点 → 当前的窗口(截断到 lookback_n 内)
    window_end = min(breakout_idx + cfg.lookback_n, len(df) - 1)
    window = df.iloc[breakout_idx:window_end + 1]
    bars_since = window_end - breakout_idx

    start_close = float(window.iloc[0]['close'])

    # 起点之后(不含起点本身)的最高 high
    after_start = window.iloc[1:]
    if len(after_start) == 0:
        return MainRallyState(
            in_rally=False,
            start_idx=breakout_idx,
            start_close=start_close,
            end_reason="刚上穿,尚未形成涨幅",
        )

    peak_high = float(after_start['high'].max())
    peak_rel_pos = int(after_start['high'].values.argmax())
    peak_idx_global = breakout_idx + 1 + peak_rel_pos

    peak_gain = (peak_high - start_close) / start_close

    # 主升浪追踪窗口是否已过期(起点距今 > N 个交易日)
    window_expired = (len(df) - 1 - breakout_idx) > cfg.lookback_n

    # 回撤计算的"当前"点:
    #   - 窗口未过期:用今天的 close(反映"现在"的回撤)
    #   - 窗口已过期:用窗口结束日的 close(反映"主升浪结束时"的回撤,避免跨期错算)
    ref_idx = search_end if not window_expired else window_end
    ref_close = float(df.iloc[ref_idx]['close'])
    drawdown = max(0.0, (peak_high - ref_close) / peak_high) if peak_high > 0 else 0.0

    # ── 检查窗口内是否出现"连续两日收盘破 MA10" ──
    # 对应文档第9课"真跌破 = 跌破当日 + 次日未收复",这是主升浪结束信号
    two_day_break = False
    two_day_break_at_idx: Optional[int] = None
    for j in range(2, len(window)):
        row_t = window.iloc[j]
        row_y = window.iloc[j - 1]
        if (pd.notna(row_t['ma10']) and pd.notna(row_y['ma10'])
                and row_t['close'] < row_t['ma10']
                and row_y['close'] < row_y['ma10']):
            two_day_break = True
            two_day_break_at_idx = breakout_idx + j
            break

    # ── ever_qualified ──
    # 起点放量上穿后,30 日窗口内峰值涨幅是否达标(不约束期间是否破 MA10)
    ever_qualified = peak_gain >= cfg.min_gain_pct

    # ── in_rally ──(当前是否仍处于主升浪)
    # 需:窗口未过期 AND 涨幅达标 AND 回撤受控 AND 未出现"连续两日破 MA10"
    in_rally = (
        not window_expired
        and peak_gain >= cfg.min_gain_pct
        and drawdown <= cfg.max_drawdown_pct
        and not two_day_break
    )

    end_reason: Optional[str] = None
    if not in_rally:
        if peak_gain < cfg.min_gain_pct:
            end_reason = f"涨幅 {peak_gain:.1%} 未达 {cfg.min_gain_pct:.0%}"
        elif two_day_break:
            end_reason = "连续两日收盘破 MA10,主升浪结束"
        elif window_expired:
            end_reason = f"已超过 {cfg.lookback_n} 日追踪窗口,主升浪已结束"
        elif drawdown > cfg.max_drawdown_pct:
            end_reason = f"从峰值回撤 {drawdown:.1%} > {cfg.max_drawdown_pct:.0%}"

    start_date = None
    try:
        start_date = str(df.index[breakout_idx])
    except Exception:
        pass

    return MainRallyState(
        in_rally=in_rally,
        ever_qualified=ever_qualified,
        start_idx=breakout_idx,
        start_date=start_date,
        start_close=start_close,
        peak_idx=peak_idx_global,
        peak_high=peak_high,
        peak_gain_pct=peak_gain,
        current_gain_pct=peak_gain,
        current_drawdown_pct=drawdown,
        bars_since_start=bars_since,
        end_reason=end_reason,
    )


def diagnose_no_rally(df: pd.DataFrame,
                      cfg: MainRallyConfig = MAIN_RALLY_CFG) -> dict:
    """诊断为什么没有识别出主升浪(给出具体不符合的指标)。

    扫描最近 lookback_n × search_lookback_mult 天内所有"close 上穿 MA10"事件
    (不要求放量),找出最接近主升浪的一次,精确指出哪一步没满足。

    Returns:
        {
          "reason_code": str,        # NO_UPCROSS / NO_VOLUME / GAIN_NOT_ENOUGH
          "reason_text": str,        # 中文说明
          "upcross_count": int,      # 30 日窗口内 close 上穿 MA10 的次数
          "best_upcross_date": str,  # 最近一次上穿日期
          "best_upcross_vol_ratio": float,  # 该次上穿日量/5日均量
          "best_upcross_max_gain": float,   # 该次上穿后 30 日内峰值涨幅
        }
    """
    info = {
        "reason_code": "UNKNOWN",
        "reason_text": "未知",
        "upcross_count": 0,
        "best_upcross_date": None,
        "best_upcross_vol_ratio": 0.0,
        "best_upcross_max_gain": 0.0,
    }

    if len(df) < 12 or 'ma10' not in df.columns or 'vol_ma5' not in df.columns:
        info["reason_code"] = "INSUFFICIENT_DATA"
        info["reason_text"] = "K线或指标数据不足"
        return info

    search_end = len(df) - 1
    search_start = max(1, search_end - cfg.lookback_n * cfg.search_lookback_mult)

    # 找出所有上穿事件(不要求放量)
    upcrosses = []
    for i in range(search_start, search_end + 1):
        prev = df.iloc[i - 1]
        today = df.iloc[i]
        if pd.isna(prev['ma10']) or pd.isna(today['ma10']):
            continue
        if prev['close'] < prev['ma10'] and today['close'] >= today['ma10']:
            upcrosses.append(i)

    info["upcross_count"] = len(upcrosses)

    if not upcrosses:
        info["reason_code"] = "NO_UPCROSS"
        info["reason_text"] = f"近 {cfg.lookback_n * cfg.search_lookback_mult} 日内 close 未上穿过 MA10"
        return info

    # 对每个上穿:看是否放量 + 之后 30 日峰值涨幅
    best_score = -1.0
    best = None
    for idx in upcrosses:
        bar = df.iloc[idx]
        vol_ratio = float(bar['volume'] / bar['vol_ma5']) if bar['vol_ma5'] > 0 else 0.0
        start_close = float(bar['close'])

        window_end = min(idx + cfg.lookback_n, len(df) - 1)
        after = df.iloc[idx + 1:window_end + 1]
        if len(after) == 0:
            max_gain = 0.0
        else:
            peak = float(after['high'].max())
            max_gain = (peak - start_close) / start_close

        # 优先级:放量上穿 > 涨幅高的非放量上穿
        score = max_gain + (0.5 if vol_ratio >= cfg.breakout_vol_mult else 0.0)
        if score > best_score:
            best_score = score
            best = (idx, vol_ratio, max_gain)

    if best is None:
        info["reason_code"] = "UNKNOWN"
        info["reason_text"] = "诊断失败"
        return info

    best_idx, best_vol, best_gain = best
    info["best_upcross_vol_ratio"] = best_vol
    info["best_upcross_max_gain"] = best_gain
    try:
        if "date" in df.columns:
            info["best_upcross_date"] = str(df.iloc[best_idx]["date"])[:10]
        else:
            info["best_upcross_date"] = str(df.index[best_idx])[:10]
    except Exception:
        pass

    if best_vol < cfg.breakout_vol_mult:
        info["reason_code"] = "NO_VOLUME"
        info["reason_text"] = (
            f"有 {len(upcrosses)} 次上穿但均未放量(最佳一次量比 {best_vol:.2f}× < {cfg.breakout_vol_mult}×)"
        )
    elif best_gain < cfg.min_gain_pct:
        info["reason_code"] = "GAIN_NOT_ENOUGH"
        info["reason_text"] = (
            f"有放量上穿但 30 日内峰值涨幅仅 {best_gain:.1%} < {cfg.min_gain_pct:.0%}"
        )
    else:
        info["reason_code"] = "EDGE_CASE"
        info["reason_text"] = "边界情况(放量+涨幅都达标但未识别,需检查算法)"

    return info


# ════════════════════════════════════════════════════════════
# 6. 资金回流 (Capital Inflow) ✅ 已锁定
#
# 定义:
#   前置: 过去 N 日内 ≥ M 天满足"弱势极限"
#         (地量 + 缩量 + close > MA20 AND close > MA60)
#
#   触发: 当日有 ≥ K 笔单笔 ≥ threshold 元的主动买大单
#         AND 当日收阳 AND 当日涨幅 ≥ 2%
#
#   语义: 沉寂之后大资金真金白银杀回来 → 主升浪起点候选
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CapitalInflowConfig:
    weak_extreme_lookback: int = 10              # 弱势极限回看窗口
    weak_extreme_min_days: int = 1               # 最少满足天数
    big_order_threshold: float = 15_000_000      # 单笔大单阈值(元),默认 1500 万
    big_order_min_count: int = 2                 # 至少几笔大单
    min_gain_pct: float = 2.0                    # 当日涨幅下限(%)
    require_close_above_open: bool = True        # 当日收阳

    # 弱势极限子条件(对应 BUY_WEAK_EXTREME 的核心子集)
    we_vol_floor_window: int = 10
    we_vol_floor_tolerance: float = 1.1
    we_vol_shrink_avg5_ratio: float = 0.75


CAPITAL_INFLOW_CFG = CapitalInflowConfig()


def capital_inflow_config_from_dict(cfg: dict | None) -> CapitalInflowConfig:
    if not cfg:
        return CAPITAL_INFLOW_CFG
    return CapitalInflowConfig(
        weak_extreme_lookback=int(cfg.get("weak_extreme_lookback", CAPITAL_INFLOW_CFG.weak_extreme_lookback)),
        weak_extreme_min_days=int(cfg.get("weak_extreme_min_days", CAPITAL_INFLOW_CFG.weak_extreme_min_days)),
        big_order_threshold=float(cfg.get("big_order_threshold", CAPITAL_INFLOW_CFG.big_order_threshold)),
        big_order_min_count=int(cfg.get("big_order_min_count", CAPITAL_INFLOW_CFG.big_order_min_count)),
        min_gain_pct=float(cfg.get("min_gain_pct", CAPITAL_INFLOW_CFG.min_gain_pct)),
        require_close_above_open=bool(cfg.get("require_close_above_open", CAPITAL_INFLOW_CFG.require_close_above_open)),
        we_vol_floor_window=int(cfg.get("we_vol_floor_window", CAPITAL_INFLOW_CFG.we_vol_floor_window)),
        we_vol_floor_tolerance=float(cfg.get("we_vol_floor_tolerance", CAPITAL_INFLOW_CFG.we_vol_floor_tolerance)),
        we_vol_shrink_avg5_ratio=float(cfg.get("we_vol_shrink_avg5_ratio", CAPITAL_INFLOW_CFG.we_vol_shrink_avg5_ratio)),
    )


def _is_weak_extreme_day(df: pd.DataFrame, i: int, cfg: CapitalInflowConfig) -> bool:
    """判定 df.iloc[i] 这一天是否满足弱势极限(地量+缩量+趋势未破)。"""
    if i < cfg.we_vol_floor_window:
        return False
    today = df.iloc[i]
    if pd.isna(today.get('ma20')) or pd.isna(today.get('ma60')) or pd.isna(today.get('vol_ma5')):
        return False
    # 地量:近 N 日最低量的 1.1 倍以内
    window_lo = max(0, i - cfg.we_vol_floor_window + 1)
    min_vol = df.iloc[window_lo:i + 1]['volume'].min()
    if min_vol <= 0 or today['volume'] > min_vol * cfg.we_vol_floor_tolerance:
        return False
    # 缩量:≤ 5日均量 × 0.75
    if today['volume'] > today['vol_ma5'] * cfg.we_vol_shrink_avg5_ratio:
        return False
    # 趋势未破
    if today['close'] < today['ma20'] or today['close'] < today['ma60']:
        return False
    return True


def detect_capital_inflow(
    df: pd.DataFrame,
    big_orders: dict | None,
    cfg: CapitalInflowConfig = CAPITAL_INFLOW_CFG,
) -> dict:
    """识别"资金回流"事件(针对 df 末根 K 线 / 大单为当日 = df 末日)。

    Args:
        df: 历史日 K(含今日,需含 ma20/ma60/vol_ma5)
        big_orders: 由 data_fetcher.get_big_orders_today() 返回的当日大单数据
        cfg: 配置

    Returns:
        {
          "is_inflow": bool,
          "weak_extreme_days_count": int,
          "big_buy_count": int,
          "big_buy_amount": float,
          "net_big_amount": float,
          "pct_change": float,
          "reason": str,
        }
    """
    if len(df) < max(cfg.we_vol_floor_window + 1, 60):
        return {"is_inflow": False, "reason": "K线数据不足"}
    if not big_orders:
        return {"is_inflow": False, "reason": "无当日大单数据"}

    # ① 前置:过去 N 日内有几天满足弱势极限(不含今天)
    end_idx = len(df) - 1
    start_idx = max(0, end_idx - cfg.weak_extreme_lookback)
    weak_days = [i for i in range(start_idx, end_idx) if _is_weak_extreme_day(df, i, cfg)]

    big_buy_count = big_orders.get("big_buy_count", 0)
    big_buy_amount = big_orders.get("big_buy_amount", 0.0)
    net_big_amount = big_orders.get("net_big_amount", 0.0)

    if len(weak_days) < cfg.weak_extreme_min_days:
        return {
            "is_inflow": False,
            "weak_extreme_days_count": len(weak_days),
            "big_buy_count": big_buy_count,
            "reason": f"前 {cfg.weak_extreme_lookback} 日内仅 {len(weak_days)} 天弱势极限,需 ≥ {cfg.weak_extreme_min_days}",
        }

    # ② 触发:当日 ≥ N 笔大单买入
    if big_buy_count < cfg.big_order_min_count:
        threshold_wan = cfg.big_order_threshold / 10000
        return {
            "is_inflow": False,
            "weak_extreme_days_count": len(weak_days),
            "big_buy_count": big_buy_count,
            "reason": f"当日 ≥{threshold_wan:.0f}万大单买入仅 {big_buy_count} 笔,需 ≥ {cfg.big_order_min_count}",
        }

    # ③ 价格响应:收阳 + 涨幅达标
    today = df.iloc[-1]
    prev = df.iloc[-2]
    pct_change = (today['close'] - prev['close']) / prev['close'] * 100 if prev['close'] > 0 else 0

    if cfg.require_close_above_open and today['close'] <= today['open']:
        return {
            "is_inflow": False,
            "weak_extreme_days_count": len(weak_days),
            "big_buy_count": big_buy_count,
            "pct_change": pct_change,
            "reason": "当日未收阳",
        }

    if pct_change < cfg.min_gain_pct:
        return {
            "is_inflow": False,
            "weak_extreme_days_count": len(weak_days),
            "big_buy_count": big_buy_count,
            "pct_change": pct_change,
            "reason": f"当日涨幅 {pct_change:.2f}% < {cfg.min_gain_pct}%",
        }

    return {
        "is_inflow": True,
        "weak_extreme_days_count": len(weak_days),
        "big_buy_count": big_buy_count,
        "big_buy_amount": big_buy_amount,
        "net_big_amount": net_big_amount,
        "pct_change": pct_change,
        "reason": (
            f"前 {len(weak_days)} 天弱势极限 + 今日 {big_buy_count} 笔 "
            f"≥{cfg.big_order_threshold/10000:.0f}万大单买入(净 {net_big_amount/10000:.0f}万) "
            f"+ 涨 {pct_change:.2f}%"
        ),
    }


# ════════════════════════════════════════════════════════════
# 9. 主流题材 (Mainstream Theme) ✅ 已锁定(v1 - 阶段 1)
#
# 定义(第10课原话):
#   主流题材 = 客观有好故事 + 主观资金认可
#
# 阶段 1 量化方案(只用现有公开数据):
#   ① 板块今日涨幅 ≥ 3%
#   ② 板块龙头(top1)涨幅 ≥ 7%
#   ③ 板块涨幅榜排名 ≤ 前 5
#
# 全部满足 = 主流题材;阶段 2~5 可后续加入(涨停数、持续性、资金流、主观面)
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MainstreamThemeConfig:
    min_sector_pct: float = 3.0        # 板块今日涨幅下限(%)
    min_leader_pct: float = 7.0        # 板块龙头涨幅下限(%)
    max_rank: int = 5                  # 板块涨幅榜排名上限


MAINSTREAM_THEME_CFG = MainstreamThemeConfig()


def mainstream_theme_config_from_dict(cfg: dict | None) -> MainstreamThemeConfig:
    if not cfg:
        return MAINSTREAM_THEME_CFG
    return MainstreamThemeConfig(
        min_sector_pct=float(cfg.get("min_sector_pct", MAINSTREAM_THEME_CFG.min_sector_pct)),
        min_leader_pct=float(cfg.get("min_leader_pct", MAINSTREAM_THEME_CFG.min_leader_pct)),
        max_rank=int(cfg.get("max_rank", MAINSTREAM_THEME_CFG.max_rank)),
    )


def detect_mainstream_theme(
    sector_overview: dict | None,
    sector_ranking: list[dict] | None,
    cfg: MainstreamThemeConfig = MAINSTREAM_THEME_CFG,
) -> dict:
    """判定一个板块是否为"主流题材"(阶段 1)。

    Args:
        sector_overview: 来自 data_fetcher.get_sector_overview()
        sector_ranking: 来自 data_fetcher.get_sector_ranking()
        cfg: 配置(阈值)

    Returns:
        {
          "is_mainstream": bool,
          "industry": str,
          "rank": int | None,
          "sector_pct": float,
          "leader_pct": float,
          "leader_name": str,
          "criteria": [{"name": str, "passed": bool, "value": str}, ...],
          "reason": str,
        }
    """
    if not sector_overview:
        return {"is_mainstream": False, "reason": "无板块数据"}

    industry = sector_overview.get("industry", "")
    sector_pct = float(sector_overview.get("pct_today", 0) or 0)
    leader_pct = float(sector_overview.get("leader_pct", 0) or 0)
    leader_name = sector_overview.get("leader_name", "")

    # 从排名找当前板块的排名
    rank = None
    if sector_ranking:
        for item in sector_ranking:
            if item.get("industry") == industry or item.get("bk_code") == sector_overview.get("bk_code"):
                rank = item.get("rank")
                break

    # 三条判定
    pass_sector = sector_pct >= cfg.min_sector_pct
    pass_leader = leader_pct >= cfg.min_leader_pct
    pass_rank = rank is not None and rank <= cfg.max_rank

    criteria = [
        {"name": "板块涨幅", "passed": pass_sector,
         "value": f"{sector_pct:+.2f}% (需 ≥ {cfg.min_sector_pct}%)"},
        {"name": "龙头涨幅", "passed": pass_leader,
         "value": f"{leader_name} {leader_pct:+.2f}% (需 ≥ {cfg.min_leader_pct}%)"},
        {"name": "板块排名", "passed": pass_rank,
         "value": f"第 {rank} (需 ≤ {cfg.max_rank})" if rank else f"未上榜 (需 ≤ {cfg.max_rank})"},
    ]
    failed = [c["name"] for c in criteria if not c["passed"]]
    is_mainstream = not failed

    if is_mainstream:
        reason = f"主流题材 ({industry}):板块{sector_pct:+.2f}% + 龙头{leader_name}{leader_pct:+.2f}% + 排名第{rank}"
    else:
        reason = f"非主流 ({industry}):缺 [{', '.join(failed)}]"

    return {
        "is_mainstream": is_mainstream,
        "industry": industry,
        "rank": rank,
        "sector_pct": sector_pct,
        "leader_pct": leader_pct,
        "leader_name": leader_name,
        "criteria": criteria,
        "reason": reason,
    }


# ════════════════════════════════════════════════════════════
# 12. 真假强势评分 (Strength Quality v2) ✅ 已锁定
#
# 用途: 判断"抗跌强势股"后续是领涨还是补跌
#   抗跌 ≠ 真强势,可能只是"无人接盘但也无人砸盘"(假强势)
#
# 9 维度评分(满分约 155):
#   A. 量价配合(±20):量缩50-80%收阳=+20,地量<30%=-10
#   B. 均线结构(±20):多头排列=+20,close破MA5=-10
#   C. 主流题材(+15):处于主流题材
#   D. 板块相对(±15):板块跌它涨=+15,板块都强它最弱=-15
#   E. 资金面(±15):大单买入≥2笔=+15,大单净流出>1000万=-15
#   F. 时间持续(+10):抗跌≥5日=+10
#   ── 弱市维度 v2 新增 ──
#   G. 逆势创新高(+25):大盘创10日新低 同时 个股创10日新高
#   H. 独立强度(+20/10/5):5日累计跑赢大盘 ≥5%/≥3%/≥0%
#   I. 板块内排名(+15/8):板块涨幅前3=+15,前10=+8
#
# 阈值: ≥ 65 = 真强势,40-65 = 观望,< 40 = 警惕补跌
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class StrengthQualityConfig:
    min_persist_days: int = 3                # 至少抗跌 N 日才评分
    healthy_vol_min: float = 0.5             # 量缩 50-80% 健康区间
    healthy_vol_max: float = 0.8
    extreme_low_vol: float = 0.3             # 地量警戒
    big_buy_min_count: int = 2               # 大单买入笔数门槛
    big_net_outflow_warn: float = 10_000_000 # 大单净流出警戒(1000万)
    long_persist_days: int = 5               # 抗跌天数加分门槛
    real_strong_threshold: int = 65          # 真强势分数线
    observe_threshold: int = 40              # 观望分数线
    # ── 弱市维度 v2 ──
    counter_trend_proximity: float = 0.005   # G: 沪指/个股逼近极值的容差(0.5%)
    relative_strong_pct: float = 5.0         # H: 强独立强度(累计跑赢大盘 ≥ 5%)
    relative_medium_pct: float = 3.0         # H: 中等独立强度
    sector_rank_top_strong: int = 3          # I: 板块前 N 名最强
    sector_rank_top_medium: int = 10         # I: 板块前 N 名中等


STRENGTH_QUALITY_CFG = StrengthQualityConfig()


def strength_quality_config_from_dict(cfg: dict | None) -> StrengthQualityConfig:
    if not cfg:
        return STRENGTH_QUALITY_CFG
    return StrengthQualityConfig(
        min_persist_days=int(cfg.get("min_persist_days", STRENGTH_QUALITY_CFG.min_persist_days)),
        healthy_vol_min=float(cfg.get("healthy_vol_min", STRENGTH_QUALITY_CFG.healthy_vol_min)),
        healthy_vol_max=float(cfg.get("healthy_vol_max", STRENGTH_QUALITY_CFG.healthy_vol_max)),
        extreme_low_vol=float(cfg.get("extreme_low_vol", STRENGTH_QUALITY_CFG.extreme_low_vol)),
        big_buy_min_count=int(cfg.get("big_buy_min_count", STRENGTH_QUALITY_CFG.big_buy_min_count)),
        big_net_outflow_warn=float(cfg.get("big_net_outflow_warn", STRENGTH_QUALITY_CFG.big_net_outflow_warn)),
        long_persist_days=int(cfg.get("long_persist_days", STRENGTH_QUALITY_CFG.long_persist_days)),
        real_strong_threshold=int(cfg.get("real_strong_threshold", STRENGTH_QUALITY_CFG.real_strong_threshold)),
        observe_threshold=int(cfg.get("observe_threshold", STRENGTH_QUALITY_CFG.observe_threshold)),
        counter_trend_proximity=float(cfg.get("counter_trend_proximity", STRENGTH_QUALITY_CFG.counter_trend_proximity)),
        relative_strong_pct=float(cfg.get("relative_strong_pct", STRENGTH_QUALITY_CFG.relative_strong_pct)),
        relative_medium_pct=float(cfg.get("relative_medium_pct", STRENGTH_QUALITY_CFG.relative_medium_pct)),
        sector_rank_top_strong=int(cfg.get("sector_rank_top_strong", STRENGTH_QUALITY_CFG.sector_rank_top_strong)),
        sector_rank_top_medium=int(cfg.get("sector_rank_top_medium", STRENGTH_QUALITY_CFG.sector_rank_top_medium)),
    )


def compute_strength_quality(
    df: pd.DataFrame,
    outperform_days: int,
    big_orders: dict | None = None,
    sector_overview: dict | None = None,
    is_mainstream: bool = False,
    # ── 弱市维度 v2 ──
    market_5d_cum_pct: float | None = None,    # 大盘近 5 日累计涨跌幅(%)
    stock_5d_cum_pct: float | None = None,     # 个股近 5 日累计涨跌幅(%),None 时自动算
    market_today_vs_10d_low: float | None = None,  # 沪指今日close / 近10日close最低 (= 1.0 即创新低)
    stock_today_vs_10d_high: float | None = None,  # 个股今日close / 近10日close最高 (= 1.0 即创新高)
    rank_in_sector: int | None = None,         # 本股在板块涨幅榜的排名(1-based)
    cfg: StrengthQualityConfig = STRENGTH_QUALITY_CFG,
) -> dict:
    """评估"真假强势" — 判断抗跌票后续是领涨还是补跌。

    Args:
        df: 历史日 K(需含 ma5/ma10/ma20/vol_ma5)
        outperform_days: 近 5 日中跑赢大盘的天数(调用方算好,0~5)
        big_orders: 当日笔级大单数据(可选)
        sector_overview: 板块概况(可选,含 pct_today/leader_pct)
        is_mainstream: 是否处于主流题材
        market_5d_cum_pct: 大盘 5 日累计涨跌幅(用于 H 独立强度判定)
        stock_5d_cum_pct: 个股 5 日累计涨跌幅(None 时自动从 df 计算)
        market_today_vs_10d_low: 沪指今日/10日最低 比值(用于 G 逆势创新高)
        stock_today_vs_10d_high: 个股今日/10日最高 比值(用于 G 逆势创新高)
        rank_in_sector: 本股在板块涨幅榜的位置(用于 I 板块排名)
        cfg: 配置

    Returns:
        {
          "score": int, "grade": str,
          "is_real_strong": bool, "outperform_days": int,
          "criteria": [{name, delta, value}, ...],
          "reason": str,
        }
    """
    if len(df) < 6:
        return {"score": 0, "grade": "数据不足", "is_real_strong": False,
                "outperform_days": outperform_days, "criteria": [], "reason": "K线<6天"}

    if outperform_days < cfg.min_persist_days:
        return {"score": 0, "grade": "未达抗跌门槛", "is_real_strong": False,
                "outperform_days": outperform_days, "criteria": [],
                "reason": f"近5日只跑赢大盘 {outperform_days} 日,需 ≥ {cfg.min_persist_days} 日"}

    today = df.iloc[-1]
    score = 0
    criteria: list[dict] = []

    # ── A. 量价配合(±20)──
    if pd.notna(today.get("vol_ma5")) and today["vol_ma5"] > 0:
        vol_ratio = float(today["volume"] / today["vol_ma5"])
        is_yang = today["close"] >= today["open"]
        if cfg.healthy_vol_min <= vol_ratio <= cfg.healthy_vol_max and is_yang:
            score += 20
            criteria.append({"name": "量缩健康+收阳", "delta": 20, "value": f"量比 {vol_ratio:.2f}"})
        elif vol_ratio < cfg.extreme_low_vol:
            score -= 10
            criteria.append({"name": "地量(无承接)", "delta": -10, "value": f"量比 {vol_ratio:.2f}"})

    # ── B. 均线结构(±20)──
    ma5 = today.get("ma5")
    ma10 = today.get("ma10")
    ma20 = today.get("ma20")
    if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20):
        if ma5 > ma10 > ma20:
            score += 20
            criteria.append({"name": "多头排列", "delta": 20,
                             "value": f"MA5({ma5:.2f})>MA10({ma10:.2f})>MA20({ma20:.2f})"})
        if today["close"] < ma5:
            score -= 10
            criteria.append({"name": "破 MA5", "delta": -10,
                             "value": f"close {today['close']:.2f} < MA5 {ma5:.2f}"})

    # ── C. 主流题材(+15)──
    if is_mainstream:
        score += 15
        criteria.append({"name": "主流题材", "delta": 15, "value": "板块入选主流"})

    # ── D. 板块相对强度(±15)──
    if sector_overview:
        sector_pct = float(sector_overview.get("pct_today", 0) or 0)
        stock_pct = float(today.get("pct_change", 0) or 0) * 100  # df 中 pct_change 是小数
        if sector_pct < -1.0 and stock_pct > 0:
            score += 15
            criteria.append({"name": "板块跌它涨", "delta": 15,
                             "value": f"板{sector_pct:+.2f}% 股{stock_pct:+.2f}%"})
        elif sector_pct > 2.0 and stock_pct < sector_pct - 2.0:
            score -= 15
            criteria.append({"name": "板块强它弱", "delta": -15,
                             "value": f"板{sector_pct:+.2f}% 股{stock_pct:+.2f}%"})

    # ── E. 资金面(±15)──
    if big_orders:
        big_buy_count = int(big_orders.get("big_buy_count", 0) or 0)
        net_big = float(big_orders.get("net_big_amount", 0) or 0)
        if big_buy_count >= cfg.big_buy_min_count:
            score += 15
            criteria.append({"name": "大单买入", "delta": 15, "value": f"{big_buy_count} 笔"})
        if net_big < -cfg.big_net_outflow_warn:
            score -= 15
            criteria.append({"name": "大单净流出", "delta": -15,
                             "value": f"净流出 {-net_big/10000:.0f} 万"})

    # ── F. 时间持续度(+10)──
    if outperform_days >= cfg.long_persist_days:
        score += 10
        criteria.append({"name": f"抗跌持续 {outperform_days} 日", "delta": 10, "value": ""})

    # ── G. 逆势创新高(+25)── 弱市真强势最硬信号
    if market_today_vs_10d_low is not None and stock_today_vs_10d_high is not None:
        market_at_low = market_today_vs_10d_low <= (1.0 + cfg.counter_trend_proximity)
        stock_at_high = stock_today_vs_10d_high >= (1.0 - cfg.counter_trend_proximity)
        if market_at_low and stock_at_high:
            score += 25
            criteria.append({
                "name": "逆势创新高",
                "delta": 25,
                "value": f"沪指/10日低={market_today_vs_10d_low:.3f} 股价/10日高={stock_today_vs_10d_high:.3f}",
            })

    # ── H. 独立强度(±20/10/5)── 累计跑赢大盘看幅度,不只看天数
    if market_5d_cum_pct is not None:
        if stock_5d_cum_pct is None and len(df) >= 6:
            prev_close = float(df.iloc[-6]["close"])
            today_close = float(df.iloc[-1]["close"])
            if prev_close > 0:
                stock_5d_cum_pct = (today_close - prev_close) / prev_close * 100
        if stock_5d_cum_pct is not None:
            relative_gain = stock_5d_cum_pct - market_5d_cum_pct
            if relative_gain >= cfg.relative_strong_pct:
                score += 20
                criteria.append({
                    "name": "独立强度(强)",
                    "delta": 20,
                    "value": f"5日累计跑赢大盘 {relative_gain:+.2f}% (股 {stock_5d_cum_pct:+.2f}% vs 大盘 {market_5d_cum_pct:+.2f}%)",
                })
            elif relative_gain >= cfg.relative_medium_pct:
                score += 10
                criteria.append({
                    "name": "独立强度(中)",
                    "delta": 10,
                    "value": f"5日累计跑赢大盘 {relative_gain:+.2f}%",
                })
            elif relative_gain >= 0:
                score += 5
                criteria.append({
                    "name": "独立强度(弱)",
                    "delta": 5,
                    "value": f"5日累计跑赢大盘 {relative_gain:+.2f}%",
                })

    # ── I. 板块内排名(+15/8)── 同板块对比的鹤立鸡群
    if rank_in_sector is not None and rank_in_sector > 0:
        if rank_in_sector <= cfg.sector_rank_top_strong:
            score += 15
            criteria.append({"name": f"板块前 {rank_in_sector} 名", "delta": 15, "value": "板块内最强"})
        elif rank_in_sector <= cfg.sector_rank_top_medium:
            score += 8
            criteria.append({"name": f"板块前 {rank_in_sector} 名", "delta": 8, "value": "板块内偏强"})

    is_real_strong = score >= cfg.real_strong_threshold
    if is_real_strong:
        grade = "🟢 真强势"
    elif score >= cfg.observe_threshold:
        grade = "🟡 观望"
    else:
        grade = "🔴 警惕补跌"

    return {
        "score": score,
        "grade": grade,
        "is_real_strong": is_real_strong,
        "outperform_days": outperform_days,
        "criteria": criteria,
        "reason": f"{grade} {score} 分",
    }


# ════════════════════════════════════════════════════════════
# 7. 真跌破 / 假跌破 (Real Break / False Break) ✅ 已锁定
#
# 定义:
#   收盘跌破: T 日 close < MA
#   真跌破:   T 日 close < MA  AND  T+1 日 close < MA
#   假跌破:   T 日 close < MA  AND  T+1 日 close ≥ MA
#   假跌破强信号: 假跌破 + T+1 日成交量 ≥ 5日均量 × 1.5
# ════════════════════════════════════════════════════════════

def is_close_break(close: float, ma_value: float) -> bool:
    """收盘跌破判定(单根 K 线)。"""
    if pd.isna(ma_value):
        return False
    return close < ma_value


def is_real_break(prev_close: float, prev_ma: float,
                   today_close: float, today_ma: float) -> bool:
    """真跌破:连续两日收盘<对应均线。"""
    if pd.isna(prev_ma) or pd.isna(today_ma):
        return False
    return prev_close < prev_ma and today_close < today_ma


def is_false_break(prev_close: float, prev_ma: float,
                    today_close: float, today_ma: float,
                    today_volume: float = 0, vol_ma5: float = 0,
                    strong_vol_mult: float = 1.5) -> tuple[bool, bool]:
    """假跌破判定。

    Returns:
        (is_false, is_strong) — 是否假跌破,以及是否伴随爆量反包(强信号)
    """
    if pd.isna(prev_ma) or pd.isna(today_ma):
        return (False, False)
    is_false = prev_close < prev_ma and today_close >= today_ma
    is_strong = False
    if is_false and vol_ma5 > 0:
        is_strong = today_volume >= vol_ma5 * strong_vol_mult
    return (is_false, is_strong)
