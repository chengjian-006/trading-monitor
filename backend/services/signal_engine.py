"""
Trading Signal Engine — 信号检测主调度 (v1.7.x).

本文件保留:
  detect_signals          : 主入口, 合并实时报价 + 调用各 detector + extra_filters
  _detect_short_signals   : per-stock 调度 — 评估所有"短线"信号 (买点/卖点/减仓/持仓警戒)
  _apply_extra_filters    : RSI/量比/涨幅 通用阈值二次过滤

实际实现按业务域拆到:
  signal_engine_config.py        — DEFAULT_SIGNAL_CONFIG / EXTRA_FILTERS / get_merged_config
  signal_engine_indicators.py    — Signal dataclass / compute_indicators
  signal_engine_detectors.py     — 各 detector + 形态/量价 helper + intraday_after + get_stock_ma_status

Signals (v1.7.90):
  BUY_WEAK_EXTREME  弱势极限（左侧）: 主升浪后地量+缩量+贴近MA10/MA20
  BUY_STRONG_START     强势起点（右侧）: 弱势极限后放量启动+涨幅≥2%+站上MA10/MA20
  SELL_BREAK_MA5  短线卖一: 持仓股跌破 MA5 ≥2%
  SELL_BREAK_MA10  短线卖二: 持仓股跌破 MA10 ≥2%
  SELL_BREAK_MA20  短线卖三: 持仓股跌破 MA20 ≥2%
  SELL_TAKE_PROFIT +7% 减仓
  SELL_TRAIL_STOP / SELL_RR_TARGET / SELL_TIME_STOP  主动止盈/止损 (默认关)
  SELL_LOSS_10  浮亏 -10% 持仓警戒 (v1.7.402: -5%/-8% 两档已按用户要求下线)
"""
import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from backend.services.intraday_estimator import (
    is_intraday, project_full_day_amount, project_full_day_volume,
)
from backend.services.signal_engine_config import (
    DEFAULT_SIGNAL_CONFIG, EXTRA_FILTERS, get_merged_config,
)
from backend.services.signal_engine_indicators import Signal, compute_indicators, _ema  # noqa: F401
from backend.services.signal_engine_detectors import (
    _count_ma5_uptrend_days,  # noqa: F401
    _is_first_touch_after_rally,  # noqa: F401
    _vol_ratio_to_recent_peak,  # noqa: F401
    _consolidation_days_near_ma,  # noqa: F401
    _consolidation_low_near_ma,  # noqa: F401
    _detect_s0_weak_extreme,
    _detect_strong_start_right,
    _detect_rally_ma20_pullback,
    _detect_vol_breakout,
    _detect_platform_breakout,
    _detect_auction_strength,
    _detect_s3_rally_pullback,  # noqa: F401
    _nearest_ma_label,  # noqa: F401
    _intraday_after,
    get_stock_ma_status,  # noqa: F401
)

logger = logging.getLogger(__name__)


def _dt_today() -> str:
    """今日日期字符串; 独立成函数供测试注入。"""
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_today_bar(d: pd.DataFrame, realtime: dict, today: str | None = None) -> pd.DataFrame:
    """末根bar日期早于今日时(日K源缺今日bar, 典型为网络源失败回退DB缓存, 缓存末根=昨日)
    用实时行情追加今日新行 — 严禁直接覆盖: 覆盖会抹掉昨日真实K线使全序列错位一天,
    涨幅/前置配对/放量倍数全算错(圣泉集团0611强势起点误触发即此因, v1.7.384)。
    供所有"实时行情并入日K"的调用点(signal_engine/scanner/near_buy)前置调用。
    """
    if today is None:
        today = _dt_today()
    if "date" in d.columns:
        last_date = str(d.iloc[-1]["date"])[:10]
        if last_date and last_date < today:
            price = float(realtime["price"])
            new_row = {c: np.nan for c in d.columns}
            new_row.update({
                "date": today,
                "open": realtime.get("open") or price,
                "high": realtime.get("high") or price,
                "low": realtime.get("low") or price,
                "close": price,
                "volume": float(realtime.get("volume") or 0),
            })
            d = pd.concat([d, pd.DataFrame([new_row])], ignore_index=True)
            # 追加是盘中常态(0612实测: 新浪日K盘中固有不含今日bar, 每轮每股都走这里),
            # 不是源降级信号 — 日志降为 debug, 健康埋点在 klines 三源全败回退缓存处
            logger.debug(f"[signal] 日K末根({last_date})早于今日({today}), 已追加今日bar而非覆盖")
    return d


def _merge_realtime_bar(d: pd.DataFrame, realtime: dict, today: str | None = None) -> pd.DataFrame:
    """把实时行情并入日K末根bar(含全天量/额外推)。today 参数仅供测试注入。"""
    d = _ensure_today_bar(d, realtime, today)
    i = d.index[-1]
    d.loc[i, "close"] = realtime["price"]
    d.loc[i, "high"] = max(d.loc[i, "high"], realtime.get("high", 0))
    d.loc[i, "low"] = min(d.loc[i, "low"], realtime.get("low", 0)) if realtime.get("low", 0) > 0 else d.loc[i, "low"]
    d.loc[i, "open"] = realtime.get("open", d.loc[i, "open"])
    # 盘中: 把累计成交量外推为全天预估量, 避免量比类指标分子偏小
    raw_vol = realtime.get("volume", d.loc[i, "volume"])
    if is_intraday():
        estimated = project_full_day_volume(raw_vol)
        d.loc[i, "volume"] = estimated if estimated is not None else raw_vol
    else:
        d.loc[i, "volume"] = raw_vol
    # v1.7.89: 同步外推全天预估成交额 (供 BUY_STRONG_START 等使用)
    raw_amt = realtime.get("amount", 0) or 0
    if is_intraday() and raw_amt > 0:
        est_amt = project_full_day_amount(raw_amt)
        d.loc[i, "amount_est"] = est_amt if est_amt is not None else raw_amt
    else:
        d.loc[i, "amount_est"] = raw_amt
    # v1.7.275: 保留原始(未外推)成交额; 9:26 竞价时点即"集合竞价成交额", 供竞价高开弱转强门槛用
    d.loc[i, "amount_now"] = raw_amt
    return d


def detect_signals(df: pd.DataFrame, trade_type: str = "short",
                   realtime: Optional[dict] = None,
                   user_config: dict | None = None,
                   entry_cost: float | None = None,
                   entry_date: str | None = None,
                   entry_model: str | None = None,
                   market_emotion: dict | None = None) -> list[Signal]:
    """主入口. 整合实时报价 → 计算指标 → 调度各 detector → 应用 extra filters.

    entry_cost: 当前持仓的加权平均成本 (来自 repository.get_holdings_cost).
                有值时 SELL_TAKE_PROFIT / SELL_LOSS_5/8/10 / SELL_TRAIL_STOP / SELL_RR_TARGET / SELL_TIME_STOP 可触发.
                None 表示未持仓或无成本数据.
    entry_date: 当前持仓的入仓日期 (来自 repository.get_holdings_entry_date).
                有值时 SELL_TRAIL_STOP (最高价回撤) / SELL_TIME_STOP (持仓天数判断) 可触发.
    entry_model: 建仓买点 (来自 repository.get_holdings_entry_model). 命中 BUY_WEAK_EXTREME 时走左侧
                差异化出场 (SELL_WEAK_STOP -12% / SELL_WEAK_TIME T+15), 并静音全部右侧卖点; 其余/None 走右侧快出.
    """
    if len(df) < 20:
        return []

    cfg = get_merged_config(user_config)
    d = compute_indicators(df, cfg)

    if realtime and realtime.get("price", 0) > 0:
        d = _merge_realtime_bar(d, realtime)
        d = compute_indicators(d, cfg)

    signals: list[Signal] = []
    latest = d.iloc[-1]
    prev = d.iloc[-2] if len(d) >= 2 else None

    # v1.7.90: 中线信号(M1/M2/MS1/MS2) 已下线; trade_type 参数保留以兼容历史调用
    if trade_type in ("short", "mid", "both"):
        rt_code = (realtime or {}).get("code")
        rt_name = (realtime or {}).get("name", "") or ""
        signals.extend(_detect_short_signals(d, latest, prev, cfg, entry_cost, entry_date, entry_model,
                                             market_emotion, code=rt_code, name=rt_name))

    signals = _apply_extra_filters(signals, latest, cfg)

    return signals


def _detect_short_signals(d: pd.DataFrame, latest: pd.Series,
                          prev: Optional[pd.Series], cfg: dict,
                          entry_cost: float | None = None,
                          entry_date: str | None = None,
                          entry_model: str | None = None,
                          market_emotion: dict | None = None,
                          code: str | None = None, name: str = "") -> list[Signal]:
    """per-stock 调度: 跑所有"短线"信号. 顺序: 持仓减仓/止盈/止损/弱势极限/强势起点/SS1-3.

    weak_exit: 该持仓由弱势极限建仓 → 走左侧出场(SELL_WEAK_STOP/-12% + SELL_WEAK_TIME/T+15),
    并静音全部右侧卖点(+7%止盈/破MA/-5/-8/-10/主动止盈止损). 回测验证: 弱势极限纯持有最优, 右侧快出砍2/3利润.
    """
    signals = []
    close = latest["close"]
    ma5 = latest["ma5"]
    ma10 = latest["ma10"]

    if pd.isna(ma10):
        return signals

    weak_exit = entry_model == "BUY_WEAK_EXTREME"

    # 护栏(v1.7.x): 持仓成本与现价偏离 >5 倍视为脏数据(如交割单列错位把成交金额写进成本,
    # 11332 vs 现价 225 → 假浮亏 -98%), 跳过成本类卖点(止盈/浮亏止损); MA 破位等非成本卖点不受影响。
    cost_valid = (entry_cost is not None and entry_cost > 0
                  and not (close > 0 and (entry_cost > close * 5 or entry_cost < close / 5)))
    if entry_cost is not None and entry_cost > 0 and not cost_valid:
        logger.warning(
            f"[sell] 持仓成本与现价偏离过大, 疑似脏数据, 跳过成本类卖点: cost={entry_cost} close={close}")

    # SELL_TAKE_PROFIT: +7%减仓 — 仅对持仓票生效, 盘中价 ≥ 成本 × (1 + target_pct)
    sc = cfg.get("SELL_TAKE_PROFIT", {})
    if not weak_exit and sc.get("enabled", True) and cost_valid:
        target_pct = sc.get("target_pct", 7.0) / 100
        if close >= entry_cost * (1 + target_pct):
            gain = (close - entry_cost) / entry_cost * 100
            signals.append(Signal(
                signal_id="SELL_TAKE_PROFIT",
                signal_name=f"止盈减仓 +{int(target_pct*100)}%",
                direction="reduce",
                detail=f"成本{entry_cost:.2f} 当前{close:.2f} 涨{gain:+.1f}% | 建议减仓50%锁利",
                strength=2,
                used_indicators=("close",),
            ))

    # ── 持仓警戒线 SELL_LOSS_10 (v1.7.402: 应用户要求砍掉 -5%/-8% 两档 ——
    # 大跌日价格逐级下穿时三档各推一次, 同一"止不止损"决策被反复催促(京东方A 0612 单日5条卖出提醒);
    # 现只保留 -10% 最终警戒一档) ──
    if not weak_exit and cost_valid:
        loss_pct = (entry_cost - close) / entry_cost * 100
        sc = cfg.get("SELL_LOSS_10", {})
        threshold = float(sc.get("threshold_pct", 10.0))
        # v1.7.422: 上涨日不报 — 现价 ≥ 昨收(当日上涨/平盘)时持仓正在回血, 不发止损催卖
        #   (同 SELL_BREAK 跌破均线口径); skip_on_up_day=False 可关闭恢复"任何时刻碰线即报"。
        up_day = float(latest.get("pct_change", 0) or 0) >= 0
        skip_up = bool(sc.get("skip_on_up_day", True))
        if sc.get("enabled", True) and loss_pct >= threshold and not (skip_up and up_day):
            signals.append(Signal(
                signal_id="SELL_LOSS_10",
                signal_name="浮亏止损 -10%",
                direction="sell",
                detail=(f"成本{entry_cost:.2f} 当前{close:.2f} 浮亏-{loss_pct:.2f}% | "
                        f"已严重超止损,建议反弹回 MA10/MA20 即卖"),
                strength=3,
                used_indicators=("close",),
            ))

    # 弱势极限 — 地量+缩量+长期/中期趋势未破+贴近 MA10 或 MA20 (v1.7.79 锚点合并)
    # v1.7.150: 前置确认 — 默认要求 T-1/T-2 也都同样满足 (避免孤日昙花)
    sc = cfg.get("BUY_WEAK_EXTREME", {})
    if sc.get("enabled", True) and _intraday_after(sc.get("intraday_earliest_minute", 600)):
        ma20 = latest.get("ma20", np.nan)
        ma60 = latest.get("ma60", np.nan)
        if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20) and not pd.isna(ma60):
            s0_result = _detect_s0_weak_extreme(d, latest, sc)
            if s0_result:
                prior_days = int(sc.get("prior_weak_days_required", 1))
                prior_ok = True
                if prior_days > 0:
                    for offset in range(1, prior_days + 1):
                        prev_idx = len(d) - 1 - offset
                        if prev_idx < 12:
                            prior_ok = False
                            break
                        if _detect_s0_weak_extreme(d.iloc[:prev_idx + 1], d.iloc[prev_idx], sc) is None:
                            prior_ok = False
                            break
                if prior_ok:
                    detail = (f"前{prior_days}日同为弱势极限 | {s0_result}" if prior_days > 0 else s0_result)
                    signals.append(Signal(
                        signal_id="BUY_WEAK_EXTREME",
                        signal_name="弱势极限（左侧）",
                        direction="buy",
                        detail=detail,
                        strength=2,
                        used_indicators=("ma10", "ma20", "ma60", "volume"),
                    ))

    # BUY_STRONG_START: 强势起点（右侧）— 弱势极限缩量基础上放量启动
    sc_ss = cfg.get("BUY_STRONG_START", {})
    if sc_ss.get("enabled", True) and _intraday_after(sc_ss.get("intraday_earliest_minute", 600)):
        ma20v = latest.get("ma20", np.nan)
        ma60v = latest.get("ma60", np.nan)
        if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20v) and not pd.isna(ma60v):
            ss_result = _detect_strong_start_right(d, latest, sc_ss, cfg.get("BUY_WEAK_EXTREME", {}),
                                                   code=code, name=name)
            if ss_result:
                signals.append(Signal(
                    signal_id="BUY_STRONG_START",
                    signal_name="强势起点（右侧）",
                    direction="buy",
                    detail=ss_result,
                    strength=3,
                    used_indicators=("ma10", "ma20", "ma60", "volume", "amount_est", "pct_change"),
                ))

    # BUY_RALLY_MA20: 主升浪回踩20MA缩量后突破昨高·缩量后突破昨高 (右侧, 补弱势极限抓不到的急跌V反)
    sc_r20 = cfg.get("BUY_RALLY_MA20", {})
    if sc_r20.get("enabled", True) and _intraday_after(sc_r20.get("intraday_earliest_minute", 600)):
        r20_result = _detect_rally_ma20_pullback(d, latest, sc_r20)
        if r20_result:
            signals.append(Signal(
                signal_id="BUY_RALLY_MA20",
                signal_name="回踩20MA缩量后突破昨高",
                direction="buy",
                detail=f"{r20_result} | 交易计划: +15%减半/-7%止损",
                strength=3,
                used_indicators=("ma20", "volume", "high"),
            ))

    # BUY_RALLY_MA10: 回踩10MA缩量后突破昨高(右侧) — 同回踩20MA缩量后突破昨高但回踩锚点MA10±1%
    sc_r10 = cfg.get("BUY_RALLY_MA10", {})
    if sc_r10.get("enabled", True) and _intraday_after(sc_r10.get("intraday_earliest_minute", 600)):
        r10_result = _detect_rally_ma20_pullback(d, latest, sc_r10)
        if r10_result:
            signals.append(Signal(
                signal_id="BUY_RALLY_MA10",
                signal_name="回踩10MA缩量后突破昨高",
                direction="buy",
                detail=f"{r10_result} | 交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停",
                strength=3,
                used_indicators=("ma10", "volume", "high"),
            ))

    # BUY_VOL_BREAKOUT: 缩量突破昨高 (右侧, 不锚均线/不要主升浪) — 1日微型平台突破
    sc_vb = cfg.get("BUY_VOL_BREAKOUT", {})
    if sc_vb.get("enabled", True) and _intraday_after(sc_vb.get("intraday_earliest_minute", 600)):
        vb_result = _detect_vol_breakout(d, latest, sc_vb, code=code, name=name)
        if vb_result:
            signals.append(Signal(
                signal_id="BUY_VOL_BREAKOUT",
                signal_name="缩量后放量突破（右侧）",
                direction="buy",
                detail=f"{vb_result} | 交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停",
                strength=3,
                used_indicators=("ma10", "ma20", "volume", "high", "amount_est"),
            ))

    # BUY_PLATFORM_BREAKOUT: 中继平台突破 (右侧, 多日横盘窄平台收盘突破上沿) — 多日平台, 区别于缩量突破的1日微型平台.
    #   硬约束: 必须收盘确认(盘中口径PF塌到1.72), 故默认尾盘14:45门槛(intraday_earliest_minute=885), 此时现价≈收盘价.
    #   退潮/分化月走平由引擎层 regime 闸门(scanner 对所有买点套 adjusted_priority_for_buy)自动降级/停发, 此处不另判.
    sc_pb = cfg.get("BUY_PLATFORM_BREAKOUT", {})
    if sc_pb.get("enabled", True) and _intraday_after(sc_pb.get("intraday_earliest_minute", 890)):
        pb_result = _detect_platform_breakout(d, latest, sc_pb)
        if pb_result:
            signals.append(Signal(
                signal_id="BUY_PLATFORM_BREAKOUT",
                signal_name="中继平台突破（右侧）",
                direction="buy",
                detail=f"{pb_result} | 交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停",
                strength=3,
                used_indicators=("ma10", "ma20", "volume", "high", "amount_est"),
            ))

    # BUY_AUCTION_STRENGTH: 竞价高开弱转强 (v1.7.275) — 强势缩量回调次日竞价高开, 9:26起触发.
    #   情绪门控: 红盘(上涨家数)≥阈值=热 或 绿盘(下跌家数)≥阈值=冰点 才放行, 中性剔除.
    #   竞价额门槛: 个股集合竞价成交额 ≥ min_auction_amount (9:26时点的原始成交额 amount_now).
    #   注: 情绪门控(涨跌家数)与竞价额门槛均未经历史回测(竞价额历史数据缺失), 上线后向前验证.
    sc_as = cfg.get("BUY_AUCTION_STRENGTH", {})
    if sc_as.get("enabled", True) and _intraday_after(sc_as.get("intraday_earliest_minute", 566)):
        # 人气门槛: 前一交易日人气排名 ≤ 100 (v1.7.407, 外挂在扫描层异步查)
        em = market_emotion or {}
        up_c = em.get("up_count")
        down_c = em.get("down_count")
        gate = int(sc_as.get("breadth_extreme", 3500))
        is_hot = up_c is not None and up_c >= gate
        is_ice = down_c is not None and down_c >= gate
        auction_amt = float(latest.get("amount_now", 0) or 0)
        amt_ok = auction_amt >= float(sc_as.get("min_auction_amount", 1e8))
        if (is_hot or is_ice) and amt_ok:
            as_result = _detect_auction_strength(d, latest, sc_as)
            if as_result:
                regime_lbl = f"热(红盘{up_c})" if is_hot else f"冰点(绿盘{down_c})"
                signals.append(Signal(
                    signal_id="BUY_AUCTION_STRENGTH",
                    signal_name="竞价高开弱转强",
                    direction="buy",
                    detail=(f"{as_result} | 竞价额{auction_amt/1e8:.2f}亿 | {regime_lbl} | "
                            f"交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停"),
                    strength=3,
                    used_indicators=("ma10", "ma20", "ma60", "volume", "open", "amount_now"),
                ))

    # S1-S4 / M1-MS2 已下线 v1.7.90 (右侧统一交给 BUY_STRONG_START)

    # ── SS1/SS2/SS3 (v1.7.91): 持仓股盘中跌破 MA5/MA10/MA20 ≥2% (任何时刻) ──
    # v1.7.178: 同时触发多档默认只推"最深破位" — 原来一次破位推 3 条卖点造成预警噪音
    #   + 3 条库记录 + 3 个撤销监测任务. 现对齐 SELL_LOSS_5/8/10 的去重.
    #   "最深"按【均线周期】定义(SS1<SS2<SS3, 跌破 MA20=丢失中期趋势最严重), 与均线
    #   排列无关: 不论多头(MA5>MA10>MA20)还是空头(MA5<MA10<MA20)排列, 跌破 MA20 都是
    #   头条事件. 故取触发中周期最长者(ss_specs 按周期升序排, 即末位), 同 PLOSS triggered[-1].
    #   (曾用"最低 anchor 值"判定, 空头排列下会误挑最轻的 SS1, 已修正.)
    #   emit_all=True 可恢复全推.
    is_holding = entry_cost is not None and entry_cost > 0
    if is_holding and not weak_exit:
        ss_specs = [
            ("SELL_BREAK_MA5", "短线卖 跌破MA5",  "ma5",  ma5,  "MA5"),
            ("SELL_BREAK_MA10", "短线卖 跌破MA10", "ma10", ma10, "MA10"),
            ("SELL_BREAK_MA20", "短线卖 跌破MA20", "ma20", latest.get("ma20", np.nan), "MA20"),
        ]
        ss_emit_all = bool(cfg.get("SELL_BREAK_MA5", {}).get("emit_all", False))
        ss_triggered = []
        # v1.7.415: "跌破"应是【向下击穿】, 而非"停在均线下方"。原判定只看 close ≤ MA×(1-break),
        #   不看方向/是否新破 → 大涨反弹日(如0615京东方A +6.10% 从下方弹回MA5)、已破多日后的反弹日
        #   都会误报"跌破MAx卖出"=卖在反弹里。加两道闸门(MA5/MA10/MA20 同口径):
        #   ① 上涨日不报: 当天 pct_change ≥ 0 多在向上(常是从下方反弹回均线), 非向下击穿。
        #   ② 新鲜击穿: 昨日尚未跌破(昨收 > 昨日同均线×(1-break)), 今日才跌破才算; 避免已破后天天重复报。
        pct_today = float(latest.get("pct_change", 0) or 0)
        prev_row = d.iloc[-2] if len(d) >= 2 else None
        for sig_id, sig_name, anchor_key, anchor_val, anchor_label in ss_specs:
            sc = cfg.get(sig_id, {})
            if not sc.get("enabled", True):
                continue
            # v1.7.403: MA5破位改尾盘确认 — 工作日14:30前(含早盘/午休)一律不判, 早盘破MA5
            # 噪音大且与"破位看收盘价"口径不符(0612京东方A 09:25用竞价价就触发了);
            # 14:30后仍破才算确认。盘后/周末放行供EOD/回测路径。MA10/MA20不受影响。
            confirm_min = int(sc.get("confirm_after_minute", 0))
            if confirm_min:
                _now = datetime.now()
                if _now.weekday() < 5 and (_now.hour * 60 + _now.minute) < confirm_min:
                    continue
            if pd.isna(anchor_val) or anchor_val <= 0:
                continue
            break_pct = float(sc.get("break_pct", 2.0)) / 100
            threshold = float(anchor_val) * (1 - break_pct)
            if close > threshold:
                continue
            # ① 上涨日不报
            if pct_today >= 0:
                continue
            # ② 新鲜击穿: 昨日同均线尚未被跌破(否则今日只是延续/反弹, 非新破)
            if prev_row is None:
                continue
            prev_anchor = float(prev_row.get(anchor_key, np.nan))
            prev_close_v = float(prev_row.get("close", np.nan))
            if pd.isna(prev_anchor) or prev_anchor <= 0 or pd.isna(prev_close_v):
                continue
            if prev_close_v <= prev_anchor * (1 - break_pct):
                continue
            drop_pct = (float(anchor_val) - close) / float(anchor_val) * 100
            ss_triggered.append({
                "sig_id": sig_id, "sig_name": sig_name, "anchor_key": anchor_key,
                "anchor_val": float(anchor_val), "anchor_label": anchor_label,
                "break_pct": break_pct, "drop_pct": drop_pct,
            })

        # 去重: 默认只保留周期最长的破位(ss_specs 末位 = 跌破的最长周期均线 = 最严重);
        #       emit_all=True 时全推. ss_triggered 按 SS1→SS2→SS3 顺序累积, 末位即最深.
        emit_ss = ss_triggered if ss_emit_all else (ss_triggered[-1:] if ss_triggered else [])
        for t in emit_ss:
            signals.append(Signal(
                signal_id=t["sig_id"],
                signal_name=t["sig_name"],
                direction="sell",
                detail=(
                    f"持仓股跌破{t['anchor_label']} ≥{t['break_pct'] * 100:.0f}% | "
                    f"close {close:.2f} ≤ {t['anchor_label']}({t['anchor_val']:.2f}) × {1 - t['break_pct']:.2f} "
                    f"(实际 -{t['drop_pct']:.2f}%)"
                ),
                strength=3,
                used_indicators=("close", t["anchor_key"]),
            ))

    # v1.7.x 主动止盈/止损 — 3 个新信号, 仅持仓票生效, 默认关闭(配置页开)
    if is_holding and not weak_exit:
        # 1) 追踪止盈: 浮盈达到 min_gain_pct 后, 持仓期最高价回撤 drawdown_pct → 减仓
        sc = cfg.get("SELL_TRAIL_STOP", {})
        if sc.get("enabled", False) and entry_date:
            min_gain_pct = float(sc.get("min_gain_pct", 5.0))
            drawdown_pct = float(sc.get("drawdown_pct", 7.0))
            history_after = d[d["date"] >= entry_date]
            if not history_after.empty:
                max_high = float(history_after["high"].max())
                if max_high > entry_cost * (1 + min_gain_pct / 100):
                    trigger_price = max_high * (1 - drawdown_pct / 100)
                    if close <= trigger_price:
                        max_gain = (max_high - entry_cost) / entry_cost * 100
                        cur_gain = (close - entry_cost) / entry_cost * 100
                        signals.append(Signal(
                            signal_id="SELL_TRAIL_STOP",
                            signal_name=f"追踪止盈-{int(drawdown_pct)}%",
                            direction="reduce",
                            detail=(
                                f"持仓最高{max_high:.2f}(浮盈+{max_gain:.1f}%) → "
                                f"回撤{drawdown_pct:.0f}%触发{trigger_price:.2f} | "
                                f"当前{close:.2f}(浮盈+{cur_gain:.1f}%) | 建议减仓50%锁利"
                            ),
                            strength=2,
                            used_indicators=("close", "high"),
                        ))

        # 2) 盈亏比止盈: 当前价 ≥ 成本 × (1 + stop_loss_pct × target_r) → 锁半仓
        sc = cfg.get("SELL_RR_TARGET", {})
        if sc.get("enabled", False):
            stop_loss_pct = float(sc.get("stop_loss_pct", 5.0))
            target_r = float(sc.get("target_r", 2.0))
            target_pct = stop_loss_pct * target_r
            if close >= entry_cost * (1 + target_pct / 100):
                gain = (close - entry_cost) / entry_cost * 100
                signals.append(Signal(
                    signal_id="SELL_RR_TARGET",
                    signal_name=f"+{int(target_pct)}%锁利({target_r:.0f}R)",
                    direction="reduce",
                    detail=(
                        f"成本{entry_cost:.2f} 当前{close:.2f} 涨{gain:+.1f}% | "
                        f"已达{target_r:.0f}R(止损{stop_loss_pct:.0f}%×{target_r:.0f}={target_pct:.0f}%) | "
                        f"建议减仓50%锁利"
                    ),
                    strength=2,
                    used_indicators=("close",),
                ))

        # 3) 时间止损: 持仓 ≥ min_days 交易日, 浮动仍在 ±flat_threshold 内 → 资金效率低
        sc = cfg.get("SELL_TIME_STOP", {})
        if sc.get("enabled", False) and entry_date:
            min_days = int(sc.get("min_days", 5))
            flat_threshold = float(sc.get("flat_threshold_pct", 3.0))
            history_after = d[d["date"] >= entry_date]
            hold_days = len(history_after)
            gain_pct = (close - entry_cost) / entry_cost * 100
            if hold_days >= min_days and abs(gain_pct) < flat_threshold:
                signals.append(Signal(
                    signal_id="SELL_TIME_STOP",
                    signal_name=f"时间止损-{min_days}日",
                    direction="reduce",
                    detail=(
                        f"持仓{hold_days}日 | 成本{entry_cost:.2f} 当前{close:.2f} "
                        f"浮动{gain_pct:+.1f}%(±{flat_threshold:.0f}%内) | "
                        f"资金效率低, 建议换股"
                    ),
                    strength=2,
                    used_indicators=("close",),
                ))

    # ── 弱势极限 左侧差异化出场 (v1.7.x): 仅对"弱势极限建仓"的持仓生效, 静音上方全部右侧卖点 ──
    # 回测(N≈2200, 真检测器重扫+样本内外验证): 弱势极限纯持有最优, 右侧快出砍2/3利润;
    # 最近半年样本内 T+15 见顶, -12% 硬止损单位保护成本低. 故: -12% 硬止损 + 持有满T+15 清仓, 不卖半.
    if is_holding and weak_exit:
        sc = cfg.get("SELL_WEAK_STOP", {})
        if sc.get("enabled", True):
            stop_pct = float(sc.get("threshold_pct", 12.0))
            # v1.7.422: 上涨日不报 — 现价 ≥ 昨收(当日上涨/平盘)不发止损催卖 (同 SELL_LOSS_10 口径)
            up_day = float(latest.get("pct_change", 0) or 0) >= 0
            skip_up = bool(sc.get("skip_on_up_day", True))
            if close <= entry_cost * (1 - stop_pct / 100) and not (skip_up and up_day):
                loss = (entry_cost - close) / entry_cost * 100
                signals.append(Signal(
                    signal_id="SELL_WEAK_STOP",
                    signal_name=f"弱势极限 止损 -{int(stop_pct)}%",
                    direction="sell",
                    detail=(
                        f"弱势极限建仓(左侧出场) | 成本{entry_cost:.2f} 当前{close:.2f} 浮亏-{loss:.2f}% | "
                        f"到左侧硬止损线 -{int(stop_pct)}%, 清仓"
                    ),
                    strength=3,
                    used_indicators=("close",),
                ))

        sc = cfg.get("SELL_WEAK_TIME", {})
        if sc.get("enabled", True) and entry_date:
            hold_cap = int(sc.get("hold_days", 15))
            history_after = d[d["date"] >= entry_date]
            hold_days = max(0, len(history_after) - 1)   # 入仓日记为 T0, 故 T+n = bars-after-entry
            if hold_days >= hold_cap:
                gain_pct = (close - entry_cost) / entry_cost * 100
                signals.append(Signal(
                    signal_id="SELL_WEAK_TIME",
                    signal_name=f"弱势极限 持有满{hold_cap}日 清仓",
                    direction="sell",
                    detail=(
                        f"弱势极限建仓(左侧出场) | 持有{hold_days}个交易日(封顶T+{hold_cap}) "
                        f"成本{entry_cost:.2f} 当前{close:.2f} 浮动{gain_pct:+.1f}% | 到封顶日, 清仓"
                    ),
                    strength=3,
                    used_indicators=("close",),
                ))

    return signals


def _apply_extra_filters(signals: list[Signal], latest: pd.Series,
                         cfg: dict) -> list[Signal]:
    """对每条信号过一遍 EXTRA_FILTERS (RSI/量比/涨幅) 阈值, 不满足的过滤掉."""
    result = []
    for sig in signals:
        sc = cfg.get(sig.signal_id, {})
        passed = True
        for fkey, fdef in EXTRA_FILTERS.items():
            if fkey not in sc:
                continue
            val = latest.get(fdef["indicator"], np.nan)
            if pd.isna(val):
                continue
            threshold = sc[fkey]
            if "scale" in fdef:
                threshold = threshold * fdef["scale"]
            if fdef["op"] == ">=" and val < threshold:
                passed = False
                break
            if fdef["op"] == "<=" and val > threshold:
                passed = False
                break
        if passed:
            result.append(sig)
    return result
