# -*- coding: utf-8 -*-
"""模拟账户持仓守护 — 盘中给模拟盘自己的持仓跑出场 (v1.7.614)。

修的是什么
──────────
模拟盘原来只买不卖。卖点信号只对「用户本人也持有」的票下发 —— scanner._filter_valid_signals 里
`if not is_hold and sig.direction != "buy": continue`(非持仓票只推买点), 而模拟盘自己买的票不在
用户的真实持仓表里 → 一条卖点都收不到。不只是成本类卖点(止盈/止损), 连不依赖成本的跌破MA5/10/20
也收不到。后果: 仓位被亏损票占死、买点全在报「资金不足」、收益曲线不代表模型表现。

怎么修
──────
本任务每 60 秒扫一遍模拟盘自己的持仓, 用**模拟盘自己的**成本/建仓日/建仓买点跑一遍生产的
signal_engine.detect_signals, 取其中的 sell/reduce, 命中就在模拟盘内成交。
两个账户(default / unlimited)持仓与成本各不相同 → 各自独立检测, 不能共用一次结果。

边界
────
- 只成交, 不推送、不落信号库、不进模型胜率统计(模拟盘是观测器, 不该污染真实信号体系)。
- 成交决策走 paper_trader.apply_exit → decide() → apply_fill, 与实盘 on_signal 同一条路径。
- 当日去重由 paper_signal_processed 兜底(同账户/同股/同卖点/同日只成交一次)。
- 与实盘 scanner 的差异: 这里没有 _STOP_CONFIRM_GUARDED 的「硬止损连续碰线5分钟才确认」延迟。
  模拟盘按 60s tick 的现价直接执行, 是「机械执行模型」的口径, 正是它要验证的东西。
"""
import logging
from datetime import datetime

from backend import data_fetcher
from backend.core.trading_calendar import is_workday
from backend.models import repository
from backend.services import paper_trader, signal_engine

logger = logging.getLogger(__name__)

WIN_START, WIN_END = "09:30", "15:00"   # 盘中窗口(与 holding_guard 同)
KLINE_DAYS = 120                        # 够算 MA60 + 主升浪窗口


async def paper_guard_tick() -> None:
    """盘中 60s: 模拟账户自有持仓的出场检测与成交。异常自吞, 不影响其它任务。"""
    if not is_workday():
        return
    hm = datetime.now().strftime("%H:%M")
    if not (WIN_START <= hm <= WIN_END):
        return
    from backend.core.config import is_production
    if not await is_production():
        return

    from backend.models.repo.paper_trading import ACCOUNT_KEYS
    # 先把两个账户的持仓收齐, 行情/日K 按 code 去重只拉一次(两账户常持有同一只票)
    books: list[tuple[dict, list]] = []
    codes: set[str] = set()
    for account_key in ACCOUNT_KEYS:
        try:
            acct = await repository.paper_get_or_create_account(1, account_key)
            positions = await repository.paper_list_positions(acct["id"])
        except Exception as e:
            logger.warning(f"[paper_guard:{account_key}] 取持仓失败, 跳过: {e}")
            continue
        if positions:
            books.append((acct, positions))
            codes.update(str(p["code"]) for p in positions)
    if not codes:
        return

    try:
        quotes = await data_fetcher.get_realtime_quotes(sorted(codes))
    except Exception as e:
        logger.warning(f"[paper_guard] 取现价失败, 本轮跳过: {e}")
        return

    klines: dict[str, object] = {}
    for code in sorted(codes):
        try:
            df = await data_fetcher.get_daily_kline(code, days=KLINE_DAYS)
            if df is not None and not df.empty and len(df) >= 20:
                klines[code] = df
        except Exception as e:
            logger.debug(f"[paper_guard] {code} 日K失败: {e}")

    try:
        user_config = await repository.get_signal_config(1)
    except Exception:
        user_config = None

    for acct, positions in books:
        try:
            await _guard_account(acct, positions, quotes, klines, user_config)
        except Exception as e:
            logger.warning(f"[paper_guard:{acct.get('account_key')}] 出场检测异常, 忽略: {e}")


async def _guard_account(acct: dict, positions: list, quotes: dict, klines: dict,
                         user_config) -> None:
    tag = acct.get("account_key", "default")
    try:
        took_half = await repository.paper_took_half_codes(acct["id"])
    except Exception:
        took_half = set()

    for pos in positions:
        code = str(pos["code"])
        qty = int(pos["qty"])
        if qty <= 0:
            continue
        q = quotes.get(code)
        df = klines.get(code)
        if not q or not q.get("price") or df is None:
            continue
        price = float(q["price"])
        if price <= 0:
            continue
        # 模拟盘自己的成本(摊薄, 含费) / 建仓日 / 建仓买点 —— 不是用户真实持仓的那一套
        entry_cost = float(pos["cost_amount"]) / qty
        entry_date = str(pos["open_date"])[:10] if pos.get("open_date") else None
        entry_model = pos.get("entry_signal_id")

        try:
            sigs = signal_engine.detect_signals(
                df, "short", q, user_config,
                entry_cost=entry_cost, entry_date=entry_date, entry_model=entry_model,
                took_half=code in took_half,
            )
        except Exception as e:
            logger.debug(f"[paper_guard:{tag}] {code} 检测异常: {e}")
            continue

        # 顺序保持引擎原样: +7%止盈(卖半) 在 跌破MA(清剩) 之前, 与模型的出场序一致
        for sig in sigs:
            if sig.direction not in ("sell", "reduce"):
                continue
            try:
                # 账户现金在成交后会变, 每笔重新取一次账户快照(decide 读 cash 定仓/记 cash_after)
                fresh = await repository.paper_get_or_create_account(1, tag)
                await paper_trader.apply_exit(
                    fresh, code=code, name=pos.get("name") or code,
                    signal_id=sig.signal_id, signal_name=sig.signal_name,
                    direction=sig.direction, price=price)
            except Exception as e:
                logger.warning(f"[paper_guard:{tag}] {code} {sig.signal_id} 成交失败: {e}")
                continue
            # 已清仓则该股后续卖点无意义
            try:
                still = await repository.paper_get_position(acct["id"], code)
            except Exception:
                still = None
            if not still or int(still["qty"]) <= 0:
                break
