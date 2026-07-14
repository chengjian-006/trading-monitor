import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import numpy as np

from backend.core.config import load_config
from backend.core.websocket import ws_manager
from backend.models import repository
from backend import data_fetcher
from backend.services import signal_engine, notifier
from backend.services import alert_throttle
from backend.services import regime_filter

logger = logging.getLogger(__name__)


def _extract_indicators(df: pd.DataFrame, rt: dict | None = None,
                        keys: tuple = ()) -> dict:
    if df.empty or len(df) < 5:
        return {}
    d = signal_engine.compute_indicators(df)
    if rt and rt.get("price", 0) > 0:
        d = signal_engine._ensure_today_bar(d, rt)   # v1.7.384: 末根=昨日时追加今日行, 防覆盖
        i = d.index[-1]
        d.loc[i, "close"] = rt["price"]
        d.loc[i, "volume"] = rt.get("volume", d.loc[i, "volume"])
        d = signal_engine.compute_indicators(d)
    latest = d.iloc[-1]

    def _val(key):
        v = latest.get(key, np.nan)
        if pd.isna(v):
            return None
        return round(float(v), 3)

    # v1.7.387: "昨日bar"指纹 — 检测器当时认的昨日日期/昨收, 供EOD自动复核精确判别序列错位。
    # 仅实时合并路径有意义(此时末根=今日, -2=昨日); 无实时行情时末根归属不确定, 不落指纹。
    fingerprint = {}
    if rt and rt.get("price", 0) > 0 and len(d) >= 2 and "date" in d.columns:
        prev = d.iloc[-2]
        try:
            fingerprint = {"prev_bar_date": str(prev["date"])[:10],
                           "prev_close": round(float(prev["close"]), 3)}
        except (TypeError, ValueError, KeyError):
            fingerprint = {}

    if keys:
        result = {"close": _val("close")}
        for k in keys:
            result[k] = _val(k)
        result.update(fingerprint)
        return result

    return {
        "close": _val("close"),
        "ma5": _val("ma5"),
        "ma10": _val("ma10"),
        "ma20": _val("ma20"),
        "ma60": _val("ma60"),
        "vol_ratio_5": _val("vol_ratio_5"),
        "vol_ratio_20": _val("vol_ratio_20"),
        "pct_change": _val("pct_change"),
        "rsi": _val("rsi"),
        "dif": _val("dif"),
        "dea": _val("dea"),
        **fingerprint,
    }


# v1.7.x: 信号元数据统一在 signal_specs.py, 这里只做 user_config 覆盖封装
from backend.services import signal_specs


def _signal_priority(sig) -> int:
    return signal_specs.priority_of(sig.signal_id, getattr(sig, "strength", 2))


def _alert_timing(sig_id: str, user_config: dict | None) -> str:
    """返回 'intraday' 或 'post_close'. user_config 优先, 否则按 signal_specs 默认。"""
    sc = (user_config or {}).get(sig_id, {}) or {}
    if isinstance(sc, dict) and "alert_timing" in sc:
        return sc["alert_timing"]
    return signal_specs.default_alert_timing(sig_id)


from backend.utils.formatting import fmt_amount as _fmt_amount  # 统一格式化(原本地副本已抽到 utils)


def _is_st_stock(stock: dict) -> bool:
    """判断是否为 ST / *ST 个股 (按名称前缀)。

    A股 ST 个股名称统一带 "ST" 前缀:
        ST xxx   = 连续2年亏损/被特别处理
        *ST xxx  = 退市风险警示
        N开头/C开头是次新股标识, 与 ST 无关, 不过滤
    """
    name = (stock.get("name") or "").upper().strip()
    # 直接子串匹配, 覆盖 ST/ *ST/ S*ST 等所有变体
    return "ST" in name


def _merge_sell_revoke(items: list[dict]) -> str:
    """卖点撤销 合并: 按股票代码聚合子信号 (同股的短线卖一/二/三 等并入一段),
    再把不同股票拼成一条消息。
    """
    if not items:
        return ""
    # 按 code 聚合
    by_code: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    for it in items:
        c = it["code"]
        if c not in by_code:
            order.append(c)
        by_code[c].append(it)

    def _format_one_stock(code: str, sub_items: list[dict]) -> str:
        first = sub_items[0]
        name = first["name"]
        original_price = first["original_price"]
        new_price = first["new_price"]
        recovery_pct = first["recovery_pct"]
        sig_names = "、".join(dict.fromkeys(s["signal_name"] for s in sub_items))  # 去重保序
        original_time = first.get("original_time") or ""
        head = [
            f"【卖点撤销】▲ {sig_names} 已恢复",
            f"{name}({code})  原提醒卖出价 {original_price:.2f} → 现 {new_price:.2f} ({recovery_pct:+.2f}%)",
            "",
        ]
        if len(sub_items) == 1:
            # 单子信号: 保留完整原预警引用块
            head.append(f"┌─ 原预警 · {original_time or '稍早'} ─")
            for seg in (first.get("original_detail") or "").split("|"):
                seg = seg.strip()
                if seg:
                    head.append(f"│ {seg}")
            head.append("└─────────────")
        else:
            # 多子信号: 每个子信号一段引用
            for s in sub_items:
                head.append(f"┌─ {s['signal_name']} · {s.get('original_time') or '稍早'} ─")
                for seg in (s.get("original_detail") or "").split("|"):
                    seg = seg.strip()
                    if seg:
                        head.append(f"│ {seg}")
                head.append("└─────────────")
        head.append("")
        head.append("现已不再满足卖出条件 — 价格修复")
        return "\n".join(head)

    if len(order) == 1:
        return _format_one_stock(order[0], by_code[order[0]])
    # 多股: 顶部摘要 + 每股一段
    blocks = [_format_one_stock(c, by_code[c]) for c in order]
    header = f"【卖点撤销 · 汇总】 近15分钟 {len(order)} 只个股 / {len(items)} 条已恢复\n"
    return header + "\n\n──────────\n\n".join(blocks)


alert_throttle.register("SELL_REVOKE", _merge_sell_revoke)


# ── 卖点撤销监测 全局 worker (v1.7.x) ──
# 把 N 个独立 _recheck_sell_signal 协程合并为一个全局 worker:
#   - 队列 {(code, signal_id, user_id) → _RecheckItem}, 同 key 不重复入队
#   - worker 每 30s 跑一次, 按 code 去重批量 fetch K线+报价, 再逐 item detect
#   - 撤销命中 → 入 alert_throttle SELL_REVOKE 队列, 移出 recheck 队列
#   - 窗口过期 / 离开交易时段 → 移出 / 清空; 队列空 worker 自然退出, 下次 enqueue 时 lazy 重启
# 收益: N 个卖出信号并发监测时, K线/报价 fetch 从 O(N) 次/30s 降到 O(unique_codes) 次/30s


from dataclasses import dataclass as _dataclass


@_dataclass
class _RecheckItem:
    code: str
    name: str
    user_id: int
    signal_id: str
    signal_name: str
    trade_type: str
    original_price: float
    original_detail: str
    original_time: str
    start_ts: float
    total_window_sec: int = 1800


_recheck_queue: dict[tuple[str, str, int], _RecheckItem] = {}
_recheck_lock = asyncio.Lock()
_recheck_worker_task: asyncio.Task | None = None
RECHECK_INTERVAL_SEC = 30


async def _enqueue_recheck(
    code: str, name: str, user_id: int,
    signal_id: str, signal_name: str,
    trade_type: str, original_price: float,
    original_detail: str = "", original_time: str = "",
    total_window_sec: int = 1800,
) -> None:
    """加入待 recheck 队列, 触发或确保 worker 在跑。同 (code, signal_id, user_id) 幂等。"""
    key = (code, signal_id, user_id)
    async with _recheck_lock:
        if key in _recheck_queue:
            return  # 已在监测中, 跳过避免重置 start_ts
        _recheck_queue[key] = _RecheckItem(
            code=code, name=name, user_id=user_id,
            signal_id=signal_id, signal_name=signal_name,
            trade_type=trade_type,
            original_price=float(original_price),
            original_detail=original_detail or "",
            original_time=original_time or "",
            start_ts=time.time(),
            total_window_sec=total_window_sec,
        )
    _ensure_recheck_worker()


def _ensure_recheck_worker() -> None:
    global _recheck_worker_task
    if _recheck_worker_task is None or _recheck_worker_task.done():
        _recheck_worker_task = asyncio.create_task(_recheck_worker_loop())


async def _recheck_worker_loop() -> None:
    """全局 worker: 每 30s 批量 fetch + 逐 item detect, 替代 N 个独立协程。"""
    while True:
        try:
            await asyncio.sleep(RECHECK_INTERVAL_SEC)

            if not _is_trading_time():
                async with _recheck_lock:
                    if _recheck_queue:
                        logger.info(f"[recheck_worker] 离开交易时段, 清空 {len(_recheck_queue)} 条待监测")
                        _recheck_queue.clear()
                # 离开交易时段后队列必然空, worker 退出, 下次 enqueue 时再起来
                return

            async with _recheck_lock:
                items_snapshot = list(_recheck_queue.items())
            if not items_snapshot:
                # 队列空, worker 退出, 下次 enqueue 时 lazy 重启
                return

            now = time.time()
            codes = sorted({it.code for _, it in items_snapshot})

            try:
                quotes = await data_fetcher.get_realtime_quotes(codes)
            except Exception as e:
                logger.warning(f"[recheck_worker] quotes 失败, 本轮跳过: {e}")
                continue

            klines: dict[str, pd.DataFrame] = {}

            async def _fetch_k(c: str):
                try:
                    df = await data_fetcher.get_daily_kline(c, days=120)
                    if df is not None and not df.empty and len(df) >= 20:
                        klines[c] = df
                except Exception as e:
                    logger.debug(f"[recheck_worker] {c} K线失败: {e}")

            await asyncio.gather(*[_fetch_k(c) for c in codes])

            user_cfg_cache: dict[int, dict | None] = {}
            to_remove: list[tuple] = []

            for key, it in items_snapshot:
                # 窗口到期
                if now - it.start_ts >= it.total_window_sec:
                    logger.info(f"[recheck_worker] {it.name}({it.code}) {it.signal_name} 监测 {it.total_window_sec//60}min 到期, 未撤销")
                    to_remove.append(key)
                    continue
                df = klines.get(it.code)
                if df is None:
                    continue
                rt = quotes.get(it.code)
                if it.user_id not in user_cfg_cache:
                    try:
                        user_cfg_cache[it.user_id] = await repository.get_signal_config(it.user_id)
                    except Exception:
                        user_cfg_cache[it.user_id] = None
                try:
                    detected = signal_engine.detect_signals(df, it.trade_type, rt, user_cfg_cache[it.user_id])
                except Exception as e:
                    logger.debug(f"[recheck_worker] detect_signals {it.code} 失败: {e}")
                    continue

                still_hit = any(s.signal_id == it.signal_id for s in detected)
                if still_hit:
                    continue  # 信号仍在, 继续监测

                # 撤销 → 入 alert_throttle 队列, 移出 recheck
                new_price = float(rt["price"]) if rt and rt.get("price") else float(df.iloc[-1]["close"])
                recovery_pct = (new_price - it.original_price) / it.original_price * 100 if it.original_price > 0 else 0
                elapsed_min = max(int((now - it.start_ts) / 60), 1)
                try:
                    await alert_throttle.enqueue("SELL_REVOKE", {
                        "code": it.code,
                        "name": it.name,
                        "signal_id": it.signal_id,
                        "signal_name": it.signal_name,
                        "original_price": float(it.original_price),
                        "new_price": float(new_price),
                        "recovery_pct": float(recovery_pct),
                        "original_detail": it.original_detail,
                        "original_time": it.original_time or f"{elapsed_min}分钟前",
                    })
                    logger.info(f"[recheck_worker] 卖点撤销入队: {it.name}({it.code}) {it.signal_name} ({elapsed_min}min 后修复)")
                except Exception as e:
                    logger.warning(f"[recheck_worker] enqueue 失败 {it.code}: {e}")
                to_remove.append(key)

            if to_remove:
                async with _recheck_lock:
                    for k in to_remove:
                        _recheck_queue.pop(k, None)
        except Exception as e:
            logger.exception(f"[recheck_worker] 异常: {e}")


from backend.core.trading_calendar import is_trading_time as _is_trading_time  # v1.7.x 统一来源


async def _compute_regime_safe() -> dict:
    """大盘 regime filter — 失败时退化为 friendly 放行, 不阻塞扫描主流程."""
    try:
        return await regime_filter.compute_regime()
    except Exception as e:
        logger.warning(f"[regime] 计算失败, 视为 friendly 放行: {e}")
        return {"regime": "friendly", "score": 50}


def _select_scan_targets(all_stocks: list[dict]) -> dict[str, list[dict]]:
    """从全量股票池选出扫描对象, 按 code 聚合成 {code: [stock_entry_per_user, ...]}.
    v1.7.589: 在池即扫(watch/focused/hold 全收) — 加自选即默认预警, 不再要求"关注"。
    跳过 trade_type='index' 的概念指数(不参与模型计算和预警)."""
    by_code: dict[str, list[dict]] = defaultdict(list)
    for s in all_stocks:
        if s.get("trade_type") == "index":
            continue
        by_code[s["code"]].append(s)
    return by_code


async def _prefetch_quotes_klines(codes: list[str]) -> tuple[dict, dict]:
    """一次性并发拉所有 codes 的实时报价 + 日 K (120 根); 限并发 3, 每只票 300ms 间隔."""
    quotes = await data_fetcher.get_realtime_quotes(codes)
    sem = asyncio.Semaphore(3)

    async def _fetch_kline(code: str):
        async with sem:
            result = code, await data_fetcher.get_daily_kline(code, days=120)
            await asyncio.sleep(0.3)
            return result

    kline_results = await asyncio.gather(*[_fetch_kline(c) for c in codes])
    kline_map = {code: df for code, df in kline_results}
    return quotes, kline_map


class _UserContextCache:
    """per-user 配置/成本/入仓日 lazy-load 缓存, 避免一轮扫描里对 N 用户重复查 DB."""
    def __init__(self):
        self.configs: dict[int, dict | None] = {}
        self.cost_maps: dict[int, dict[str, float]] = {}
        self.entry_date_maps: dict[int, dict[str, str]] = {}
        self.entry_model_maps: dict[int, dict[str, str]] = {}
        self.took_half_sets: dict[int, set[str]] = {}

    async def get(self, user_id: int, code: str, is_hold: bool) -> tuple[dict | None, float | None, str | None, str | None, bool]:
        """返回 (user_config, entry_cost, entry_date, entry_model, took_half). 非持仓票后四项为空。"""
        if user_id not in self.configs:
            self.configs[user_id] = await repository.get_signal_config(user_id)
        if user_id not in self.cost_maps:
            # 三件套一次 FIFO 算齐: 分调三个函数会同一轮内 3 次全量拉交割单重算
            cost_map, date_map, model_map = await repository.get_holdings_full_info(user_id)
            self.cost_maps[user_id] = cost_map
            self.entry_date_maps[user_id] = date_map
            self.entry_model_maps[user_id] = model_map
            self.took_half_sets[user_id] = await repository.get_holdings_took_half(user_id, date_map)
        entry_cost = self.cost_maps[user_id].get(code) if is_hold else None
        entry_date = self.entry_date_maps[user_id].get(code) if is_hold else None
        entry_model = self.entry_model_maps[user_id].get(code) if is_hold else None
        took_half = (code in self.took_half_sets[user_id]) if is_hold else False
        return self.configs[user_id], entry_cost, entry_date, entry_model, took_half


# v1.7.x: 买点质量序(越小越优先保留), 多买点共振时留最强的一条
_BUY_QUALITY_ORDER = {
    "BUY_AUCTION_STRENGTH": 0,
    "BUY_VOL_BREAKOUT": 1,
    "BUY_RALLY_MA10": 2,
    "BUY_RALLY_MA20": 3,
    "BUY_RALLY_MA60": 4,
    "BUY_STRONG_START": 5,
    "BUY_WEAK_EXTREME": 6,
}


# ── 碰线类卖点"确认延迟"护栏 (v1.7.421 起, v1.7.425 扩到跌破MA) ──
# 这类卖点是"任一时刻碰线即报", 早盘插针会让人卖在针里, 故加确认延迟:
#   首次碰线不立刻推, 挂起观察; 连续碰线满 confirm_persist_sec 才真推; 期间收复(信号
#   消失)则静默丢弃、从不推送 —— 避免 v1.7.345 停用的"卖出→撤销"双消息噪音。
# 覆盖:
#   - 硬止损 SELL_LOSS_10 / SELL_WEAK_STOP (v1.7.421): 下影线插针(0615 沪电-10.78%收-3.77%、
#     阳光-12.02%收-10.99%)误推。
#   - 跌破MA5/10/20 SELL_BREAK_MA* (v1.7.425 方案A): "上涨日不报+新鲜击穿"两闸是瞬时快照判断,
#     挡不住开盘插针 —— 0616 京东方A 09:33 瞬时-0.84%擦破MA10即报、09:35收复全天收+4.22%,
#     与止损插针同病根, 复用同一确认延迟机制收口。
# 纯内存(进程级), 自愈: 两次碰线间隔 > GAP_RESET 视为新一轮重新计时(防价格在线附近反复横跳)。
_STOP_CONFIRM_GUARDED = {
    "SELL_LOSS_10", "SELL_WEAK_STOP",
    "SELL_BREAK_MA5", "SELL_BREAK_MA10", "SELL_BREAK_MA20",
}
_STOP_CONFIRM_DEFAULT_SEC = 300        # 默认连续碰线 5min 才确认 (可被 user_config 同名键覆盖)
_STOP_CONFIRM_GAP_RESET_SEC = 120      # 碰线间隔超 2min 视为新一轮(收复后再跌穿重新计时)
_stop_confirm_state: dict[tuple, tuple[float, float]] = {}  # (user,code,sig) -> (first_ts, last_ts)


def _stop_confirm_ok(user_id: int, code: str, signal_id: str,
                     confirm_sec: float, now: float | None = None) -> bool:
    """硬止损确认延迟: 是否已"连续碰线"满 confirm_sec。confirm_sec<=0 关闭(立即放行)。

    每次碰线扫描调用一次: 首次/间隔过大→记起点返 False; 续命→更新 last 并按累计时长判定。
    """
    if confirm_sec <= 0:
        return True
    now = now if now is not None else time.time()
    key = (user_id, code, signal_id)
    rec = _stop_confirm_state.get(key)
    if rec is None or (now - rec[1]) > _STOP_CONFIRM_GAP_RESET_SEC:
        _stop_confirm_state[key] = (now, now)   # 新一轮: 首次碰线
        return False
    first, _last = rec
    _stop_confirm_state[key] = (first, now)     # 续命(更新最近碰线时刻)
    return (now - first) >= confirm_sec


async def _filter_valid_signals(signals, user_config: dict | None, *,
                                 is_hold: bool,
                                 code: str, user_id: int,
                                 sent_today: tuple[set, set] | None = None) -> list:
    """过滤 — post_close 时机分流 + 非持仓仅买点 + 当日去重(同信号 + 跨买点) + 多买点共振合并.

    sent_today=(已推信号键集, 已推买点股集), 由调用方每轮预载一次, 替代逐信号 N+1 查询;
    传 None 时回退到逐条 DB 查询(兼容未预载的调用方)。
    """
    valid = []
    sent_keys, buy_sent_keys = sent_today if sent_today is not None else (None, None)
    buy_blocked: bool | None = None   # 懒查(仅 fallback 用): 该股当日是否已推过任意买点
    for sig in signals:
        # v1.7.35: alert_timing=post_close 的信号交给 15:05 汇总任务
        if _alert_timing(sig.signal_id, user_config) == "post_close":
            continue
        # v1.7.589: 非持仓票只推买点(卖点对没持仓的票无意义, 成本类卖点也算不出)
        if not is_hold and sig.direction != "buy":
            continue
        if sent_keys is not None:
            if (user_id, code, sig.signal_id) in sent_keys:
                continue
        elif await repository.signal_already_sent_today(code, sig.signal_id, user_id):
            continue
        # v1.7.421: 成本类硬止损确认延迟 — 首次碰线挂起, 连续碰线满阈值才放行, 防早盘插针误推
        if sig.signal_id in _STOP_CONFIRM_GUARDED:
            sc = (user_config or {}).get(sig.signal_id, {}) or {}
            confirm_sec = sc.get("confirm_persist_sec")
            if confirm_sec is None:
                confirm_sec = _STOP_CONFIRM_DEFAULT_SEC
            if not _stop_confirm_ok(user_id, code, sig.signal_id, float(confirm_sec)):
                continue
        # v1.7.x: 跨买点当日去重 — 同股当天已有任意买点, 后续买点不再重复推(减噪)
        if sig.direction == "buy":
            if buy_sent_keys is not None:
                if (user_id, code) in buy_sent_keys:
                    continue
            else:
                if buy_blocked is None:
                    buy_blocked = await repository.buy_signal_already_sent_today(code, user_id)
                if buy_blocked:
                    continue
        valid.append(sig)

    # v1.7.x: 同一次扫描多个买点共振 → 只保留最强一条, 其余名称并入 detail("多买点共振")
    buys = [s for s in valid if s.direction == "buy"]
    if len(buys) > 1:
        buys.sort(key=lambda s: _BUY_QUALITY_ORDER.get(s.signal_id, 99))
        keep, others = buys[0], buys[1:]
        keep.detail = f"{keep.detail} | 多买点共振(另触发: {'/'.join(s.signal_name for s in others)})"
        drop_ids = {id(s) for s in others}
        valid = [s for s in valid if id(s) not in drop_ids]
    return valid


async def _emit_one_signal(sig, *, code: str, name: str, df, rt,
                            user_id: int, trade_type: str,
                            price: float, stock_pct: float, strategy: str,
                            now_str: str, amount_suffix: str,
                            regime_label: str) -> tuple[str, int]:
    """单条信号: 计算 priority + regime 调整 + 写库 + WS 推前端 + 30min 撤销监测.

    Returns: (final_detail, final_priority)
    """
    detail = f"{sig.detail}{amount_suffix}"
    priority = _signal_priority(sig)
    if sig.direction == "buy":
        adjusted = regime_filter.adjusted_priority_for_buy(priority, regime_label)
        if adjusted != priority:
            detail = f"{detail} | 大盘{regime_label}降级"
            priority = adjusted

    indicators = _extract_indicators(df, rt, sig.used_indicators)

    # 所有档位都写库 (留痕供 outcome 回填/学习)
    await repository.save_signal(
        code=code, name=name,
        signal_id=sig.signal_id,
        signal_name=sig.signal_name,
        direction=sig.direction,
        price=price, detail=detail,
        user_id=user_id, indicators=indicators,
        signal_group=signal_specs.group_of(sig.signal_id),
    )
    # 模拟账户: 个股买卖点实时模拟成交(仅生产环境, 异常自吞)
    from backend.services import paper_trader
    await paper_trader.on_signal(
        code=code, name=name, signal_id=sig.signal_id, signal_name=sig.signal_name,
        direction=sig.direction, price=price, user_id=user_id,
    )

    # WS: hostile-buy 静默, 其他都推
    silent_ws = (regime_label == "hostile" and sig.direction == "buy")
    if not silent_ws:
        await ws_manager.send_to_user(user_id, {
            "type": "signal",
            "code": code, "name": name,
            "signal_id": sig.signal_id,
            "signal_name": sig.signal_name,
            "direction": sig.direction,
            "price": price, "pct_change": stock_pct,
            "detail": detail, "strategy": strategy,
            "triggered_at": now_str, "time": now_str[11:],
            "priority": priority,
        })

    tier_label = {3: "强", 2: "中", 1: "弱"}.get(priority, "?")
    logger.info(f"Signal[{tier_label}]: [{sig.direction}] {name}({code}) {sig.signal_name} -> user {user_id}")

    # 卖点撤销监测已停用 (v1.7.345): 不再排 30min 回头监测, 故不再发「卖点撤销·价格修复」推送。
    # 全局 recheck worker / _merge_sell_revoke 合并器保留为休眠死代码, 如需恢复把下面入队解开即可。
    # if priority >= 3 and sig.direction == "sell":
    #     await _enqueue_recheck(
    #         code=code, name=name, user_id=user_id,
    #         signal_id=sig.signal_id, signal_name=sig.signal_name,
    #         trade_type=trade_type, original_price=float(price),
    #         original_detail=detail, original_time=now_str[11:16],
    #     )
    return detail, priority


async def _push_strong_wechat(strong_items: list, *, code: str, name: str,
                               user_id: int, price: float, stock_pct: float,
                               strategy: str, amount_suffix: str, now_str: str):
    """强档企微推送: 单信号走 send_wechat_signal, 多信号合并文本."""
    # v1.7.569: 合并推送前套推送偏好闸(与单信号 send_wechat_signal 同口径) — 原来合并分支直接
    #   走 send_wechat_text 绕过闸门, 用户静音的票/关掉的模型只要同tick撞上多个强档就照推。
    #   逐信号过滤 snooze/model_off/ack; 全被抑制则不推; 今日免打扰(mute)只静音飞书。
    mute_lark = False
    try:
        from backend.services import push_pref as _pref_svc
        from backend.models.repo import push_pref as _pref_repo
        _prefs = await _pref_repo.active_prefs(user_id or 1)
        _kept = []
        for _sig, _detail in strong_items:
            _v = _pref_svc.decide(_prefs, code, _sig.signal_id)
            if _v["suppress_all"]:
                logger.info(f"[push_pref] 抑制(合并){_v['reason']}: {name}({code}) {_sig.signal_name}")
                continue
            mute_lark = mute_lark or _v["mute_lark"]
            _kept.append((_sig, _detail))
        strong_items = _kept
    except Exception as e:
        logger.warning(f"[push_pref] 合并推送闸门异常, 放行: {e}")
    if not strong_items:
        return

    if len(strong_items) == 1:
        sig, detail = strong_items[0]
        model_stats = None
        if sig.direction == "buy":
            try:
                from backend.services.buy_model_stats import get_buy_model_stats
                model_stats = (await get_buy_model_stats()).get(sig.signal_id)
            except Exception as e:
                logger.warning(f"[scan] 取买点战绩失败: {e}")
        await notifier.send_wechat_signal(
            code=code, name=name,
            signal_name=sig.signal_name,
            direction=sig.direction,
            price=price, detail=detail,
            user_id=user_id, strategy=strategy,
            pct_change=stock_pct,
            model_stats=model_stats,
            signal_id=sig.signal_id,
        )
        return

    has_sell = any(s.direction == "sell" for s, _ in strong_items)
    arrow = "▼" if has_sell else "▲"
    label = "多信号触发" if has_sell else "多买点叠加"
    pct_str = f"+{stock_pct:.2f}%" if stock_pct >= 0 else f"{stock_pct:.2f}%"
    lines = [f"【{label}】{arrow} {name}({code})  {price:.2f} {pct_str}{amount_suffix}", ""]
    # v1.7.569: 合并推送补 RED/YELLOW 市场风险标记(原来只有单信号 send_wechat_signal 有, 合并分支丢了)
    if any(s.direction == "buy" for s, _ in strong_items):
        try:
            from backend.services.market_risk_controller import get_risk_state
            _rs = await get_risk_state()
            if _rs == "RED":
                lines.insert(1, "⚠️ 市场风险·空仓预警(RED): 回测期内信号胜率30%均值-3.6%, 强烈建议停开新仓")
            elif _rs == "YELLOW":
                lines.insert(1, "⚡ 市场风险·谨慎(YELLOW): 轻度预警, 注意风控")
        except Exception:
            pass
    # 多买点合并推送也带上各买点历史胜率(与单条推送口径一致)
    stats_map = {}
    if any(s.direction == "buy" for s, _ in strong_items):
        try:
            from backend.services.buy_model_stats import get_buy_model_stats
            stats_map = await get_buy_model_stats()
        except Exception as e:
            logger.warning(f"[scan] 取买点战绩失败(合并推送): {e}")
    for sig, detail in strong_items:
        sig_arrow = "▼" if sig.direction == "sell" else ("▲" if sig.direction == "buy" else "•")
        lines.append(f"┌─ {sig_arrow} {sig.signal_name} · {now_str[11:16]} ─")
        for seg in detail.split("|"):
            seg = seg.strip()
            if seg:
                lines.append(f"│ {seg}")
        if sig.direction == "buy":
            ms = stats_map.get(sig.signal_id)
            oneline = notifier.model_stats_oneline(ms)
            if oneline:
                lines.append(f"│ {oneline}")
        lines.append("└─────────────")
        lines.append("")
    if strategy:
        lines.append(f"📋 操作策略: {strategy}")
    await notifier.send_wechat_text("\n".join(lines).rstrip(), mute_lark=mute_lark)
    logger.info(f"Signal: 合并推送 {name}({code}) {len(strong_items)}个强档信号 -> user {user_id}")


async def _scan_one_stock(code: str, stock_entries: list, *,
                           df, rt, user_ctx: _UserContextCache,
                           regime_label: str, market_emotion: dict | None = None,
                           sent_today: tuple[set, set] | None = None):
    """单只票全用户处理: 跳 ST → 各用户独立 detect/filter/emit/push.

    一票上多用户的场景 (同 code 来自不同 user) 共享 df/rt.
    """
    for stock in stock_entries:
        if _is_st_stock(stock):
            continue
        user_id = stock["user_id"]
        trade_type = stock["trade_type"]
        name = stock["name"]
        is_hold = stock.get("status") == "hold"

        user_config, entry_cost, entry_date, entry_model, took_half = await user_ctx.get(user_id, code, is_hold)

        signals = signal_engine.detect_signals(
            df, trade_type, rt, user_config,
            entry_cost=entry_cost, entry_date=entry_date, entry_model=entry_model,
            market_emotion=market_emotion, took_half=took_half,
        )

        valid_sigs = await _filter_valid_signals(
            signals, user_config,
            is_hold=is_hold,
            code=code, user_id=user_id, sent_today=sent_today,
        )
        if not valid_sigs:
            continue

        # 竞价弱转强: 前一交易日人气排名 ≤ 100 (v1.7.407)
        for sig in valid_sigs:
            if sig.signal_id == "BUY_AUCTION_STRENGTH":
                try:
                    from backend.models.repo.stocks import get_latest_popularity_rank
                    rank = await get_latest_popularity_rank(code)
                    if rank is not None and rank > 100:
                        valid_sigs = [s for s in valid_sigs if s.signal_id != "BUY_AUCTION_STRENGTH"]
                        if not valid_sigs:
                            continue
                except Exception:
                    pass
                break

        if not valid_sigs:
            continue

        price = rt["price"] if rt else df.iloc[-1]["close"]
        stock_pct = float(rt.get("pct_change", 0)) if rt else 0.0
        amount = float(rt.get("amount", 0)) if rt else 0
        strategy = stock.get("strategy") or ""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        amount_suffix = f" | 成交额 {_fmt_amount(amount)}" if amount > 0 else ""

        # 逐条 emit (写库+WS+30min monitor), 收集 (sig, detail, priority)
        sig_details = []
        for sig in valid_sigs:
            detail, priority = await _emit_one_signal(
                sig, code=code, name=name, df=df, rt=rt,
                user_id=user_id, trade_type=trade_type,
                price=price, stock_pct=stock_pct, strategy=strategy,
                now_str=now_str, amount_suffix=amount_suffix,
                regime_label=regime_label,
            )
            sig_details.append((sig, detail, priority))

        # 企微推送: 仅强档 (priority=3) 参与
        strong_items = [(sig, detail) for sig, detail, p in sig_details if p >= 3]
        if not strong_items:
            continue
        await _push_strong_wechat(
            strong_items, code=code, name=name, user_id=user_id,
            price=price, stock_pct=stock_pct, strategy=strategy,
            amount_suffix=amount_suffix, now_str=now_str,
        )


# ── 缩量后放量突破 9:45 vs 10:00 闸门 A/B (v1.7.x 临时实验, 只记不推) ──
# 在 9:45-10:06 窗口对每只自选评估无门槛的"缩量后放量突破"条件; 首次命中分档记 0945/1000.
# 仅此窗口两档才有差异(其余时段两档同时触发, 无对比价值, 不记以省开销)。
_gate_ab_done: dict = {"date": "", "both": set(), "pushed": set()}


async def _log_gate_ab(code: str, name: str, df, rt) -> None:
    if rt is None or rt.get("price", 0) <= 0 or df is None or len(df) < 20:
        return
    now = datetime.now()
    cur_min = now.hour * 60 + now.minute
    if cur_min < 585 or cur_min > 606:   # 仅 9:45-10:06 窗口
        return
    today = now.strftime("%Y-%m-%d")
    if _gate_ab_done["date"] != today:
        _gate_ab_done["date"] = today
        _gate_ab_done["both"] = set()
        _gate_ab_done["pushed"] = set()
    if code in _gate_ab_done["both"]:
        return
    try:
        from backend.services.intraday_estimator import is_intraday, project_full_day_amount, project_full_day_volume
        from backend.services.signal_engine_detectors import _detect_vol_breakout
        from backend.services.signal_engine_config import DEFAULT_SIGNAL_CONFIG
        d = signal_engine.compute_indicators(df)
        d = signal_engine._ensure_today_bar(d, rt)   # v1.7.384: 末根=昨日时追加今日行, 防覆盖
        i = d.index[-1]
        d.loc[i, "close"] = rt["price"]
        d.loc[i, "high"] = max(d.loc[i, "high"], rt.get("high", 0))
        d.loc[i, "open"] = rt.get("open", d.loc[i, "open"])
        raw_vol = rt.get("volume", d.loc[i, "volume"])
        d.loc[i, "volume"] = (project_full_day_volume(raw_vol) or raw_vol) if is_intraday() else raw_vol
        raw_amt = rt.get("amount", 0) or 0
        d.loc[i, "amount_est"] = (project_full_day_amount(raw_amt) or raw_amt) if (is_intraday() and raw_amt > 0) else raw_amt
        d = signal_engine.compute_indicators(d)
        latest = d.iloc[-1]
        if _detect_vol_breakout(d, latest, DEFAULT_SIGNAL_CONFIG["BUY_VOL_BREAKOUT"]) is None:
            return
        prev_high = float(df.iloc[-2]["high"])
        trig = prev_high * 1.02
        price = float(rt["price"])
        pre = float(rt.get("pre_close", 0) or 0)
        lim = 0.20 if code[:2] in ("30", "68") else 0.10
        sealed = 1 if (pre > 0 and price >= pre * (1 + lim) - 0.01) else 0
        rec = {
            "code": code, "name": name, "trade_date": today,
            "trigger_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_price": round(price, 3), "trigger_level": round(trig, 3),
            "gap_pct": round((price / trig - 1) * 100, 2) if trig > 0 else 0,
            "amount_est_yi": round(float(latest.get("amount_est", 0) or 0) / 1e8, 2),
            "sealed": sealed,
        }
        arms = ["0945"]
        if cur_min >= 600:
            arms.append("1000")
            _gate_ab_done["both"].add(code)
        await repository.save_gate_ab(rec, arms)
        # 9:45 首次命中 → 盘中推送(标注模型测试中), 每股每日一次
        if code not in _gate_ab_done["pushed"]:
            _gate_ab_done["pushed"].add(code)
            seal_txt = "⚠️ 当前已涨停,仅供观察" if sealed else "比正式版(10:00)早15分钟"
            msg = (f"【模型测试中 · 9:45提前版】缩量后放量突破\n"
                   f"{name}({code})  现价{price:.2f}  距突破线 {rec['gap_pct']:+.1f}%\n"
                   f"{now.strftime('%H:%M')} 命中 · 全天预估成交额{rec['amount_est_yi']:.1f}亿 · {seal_txt}\n"
                   f"———\n"
                   f"这是 9:45 提前门槛的 A/B 测试信号,非正式买点,仅供你对比观察是否值得提前;正式推送仍为 10:00 版。")
            try:
                await notifier.send_wechat_text(msg)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[gate_ab] {code} {e}")


async def scan_stock_pool():
    if not _is_trading_time():
        return

    all_stocks = await repository.list_all_stocks()
    if not all_stocks:
        return

    current_regime = await _compute_regime_safe()
    regime_label = current_regime.get("regime", "friendly")

    # v1.7.275: 取一次最新情绪快照(全市场涨跌家数), 供竞价高开弱转强情绪门控用
    try:
        market_emotion = await repository.get_latest_emotion()
    except Exception:
        market_emotion = None

    by_code = _select_scan_targets(all_stocks)
    if not by_code:
        return

    quotes, kline_map = await _prefetch_quotes_klines(list(by_code.keys()))
    user_ctx = _UserContextCache()

    # v1.7.x: 每轮一次性预载今日已推信号键, 替代扫描循环里逐信号 N+1 去重查询。
    user_ids = {s["user_id"] for entries in by_code.values() for s in entries}
    sent_rows = await repository.get_sent_signal_keys_today(list(user_ids))
    sent_keys = {(r["user_id"], r["code"], r["signal_id"]) for r in sent_rows}
    buy_sent_keys = {(r["user_id"], r["code"]) for r in sent_rows if r["direction"] == "buy"}
    sent_today = (sent_keys, buy_sent_keys)

    for code, stock_entries in by_code.items():
        try:
            df = kline_map.get(code)
            if df is None or df.empty or len(df) < 20:
                continue
            rt = quotes.get(code)
            await _scan_one_stock(
                code, stock_entries,
                df=df, rt=rt, user_ctx=user_ctx,
                regime_label=regime_label, market_emotion=market_emotion,
                sent_today=sent_today,
            )
            # v1.7.x: 缩量后放量突破 9:45 vs 10:00 闸门 A/B 记录(只记不推)
            await _log_gate_ab(code, stock_entries[0].get("name", "") if stock_entries else "", df, rt)
        except Exception as e:
            logger.error(f"Error scanning {code}: {e}")
        # v1.7.412: 每只票算完让渡一次事件循环。_scan_one_stock 的同步 pandas 计算
        # 加起来约 2.6s, 不让渡会把单 worker 的事件循环整段冻住, 盘中 HTTP/进入系统卡 ~2.6s。
        # sleep(0) 把整块冻结切成单只票 (~30ms) 的小段, HTTP 请求能在两只票之间被及时服务。
        await asyncio.sleep(0)


async def manual_scan(user_id: int = 1) -> list[dict]:
    stocks = await repository.list_stocks(user_id)
    # v1.7.16: ST/*ST 个股不参与手动扫描; v1.7.589: 在池即扫(不再要求关注), 概念指数除外
    stocks = [s for s in stocks if s.get("trade_type") != "index" and not _is_st_stock(s)]
    if not stocks:
        return []

    codes = [s["code"] for s in stocks]
    quotes = await data_fetcher.get_realtime_quotes(codes)
    user_config = await repository.get_signal_config(user_id)
    cost_map = await repository.get_holdings_cost(user_id)
    entry_date_map = await repository.get_holdings_entry_date(user_id)
    entry_model_map = await repository.get_holdings_entry_model(user_id)
    took_half_set = await repository.get_holdings_took_half(user_id, entry_date_map)

    sem = asyncio.Semaphore(3)
    async def _fetch_kline(code: str):
        async with sem:
            result = code, await data_fetcher.get_daily_kline(code, 120)
            await asyncio.sleep(0.3)
            return result

    kline_results = await asyncio.gather(*[_fetch_kline(c) for c in codes])
    kline_map = {code: df for code, df in kline_results}
    all_signals = []

    for stock in stocks:
        code = stock["code"]
        is_hold = stock.get("status") == "hold"
        try:
            df = kline_map.get(code)
            if df is None or df.empty or len(df) < 20:
                continue
            rt = quotes.get(code)
            entry_cost = cost_map.get(code) if is_hold else None
            entry_date = entry_date_map.get(code) if is_hold else None
            entry_model = entry_model_map.get(code) if is_hold else None
            sigs = signal_engine.detect_signals(
                df, stock["trade_type"], rt, user_config,
                entry_cost=entry_cost, entry_date=entry_date, entry_model=entry_model,
                took_half=(code in took_half_set) if is_hold else False,
            )
            for sig in sigs:
                if not is_hold and sig.direction != "buy":
                    continue
                indicators = _extract_indicators(df, rt, sig.used_indicators)
                all_signals.append({
                    "code": code,
                    "name": stock["name"],
                    "signal_id": sig.signal_id,
                    "signal_name": sig.signal_name,
                    "direction": sig.direction,
                    "price": rt["price"] if rt else df.iloc[-1]["close"],
                    "detail": sig.detail,
                    "indicators": indicators,
                })
        except Exception as e:
            logger.error(f"Manual scan error for {code}: {e}")

    return all_signals
