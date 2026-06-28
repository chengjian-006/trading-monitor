"""盘后 15:05 信号汇总任务 (v1.7.35)

把所有标记为 alert_timing="post_close" 的信号当日命中汇总成一条企微消息。
默认覆盖: 真假强势评分、主流题材 (中线 M1/M2/MS1/MS2 已于 v1.7.90 下线)。
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime

from backend.models import repository
from backend import data_fetcher
from backend.services import signal_engine, notifier
from backend.services.scanner import _alert_timing, _is_st_stock, _fmt_amount
from backend.services.weak_extreme_scanner import (
    collect_weak_extreme_hits, build_weak_extreme_section,
)

logger = logging.getLogger(__name__)


from backend.core.trading_calendar import is_workday as _is_workday  # 统一交易日判断


async def collect_post_close_signals() -> tuple[dict, int]:
    """扫 focused/hold 票, 收集 alert_timing=post_close 的信号, 按 signal_name 分组。
    返回 (by_signal, total)。纯收集, 不推送。"""
    all_stocks = await repository.list_all_stocks()
    by_code: dict[str, dict] = {}
    for s in all_stocks:
        if not (s.get("focused") or s.get("status") == "hold"):
            continue
        if _is_st_stock(s):
            continue
        by_code.setdefault(s["code"], s)
    if not by_code:
        logger.info("[post_close] 股票池为空,跳过")
        return {}, 0

    codes = list(by_code.keys())
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception:
        quotes = {}

    sem = asyncio.Semaphore(3)

    async def _fetch_kline(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=120)
            except Exception as e:
                logger.warning(f"[post_close] {code} K线失败: {e}")
                df = None
            await asyncio.sleep(0.3)
            return code, df

    kline_results = await asyncio.gather(*[_fetch_kline(c) for c in codes])
    kline_map = dict(kline_results)

    user_config = await repository.get_signal_config(1)

    by_signal: dict[str, list[dict]] = defaultdict(list)
    for code, stock in by_code.items():
        df = kline_map.get(code)
        if df is None or df.empty or len(df) < 20:
            continue
        rt = quotes.get(code)
        try:
            signals = signal_engine.detect_signals(df, "both", rt, user_config)
        except Exception as e:
            logger.warning(f"[post_close] {code} 检测失败: {e}")
            continue

        price = rt["price"] if rt and rt.get("price") else float(df.iloc[-1]["close"])
        pct = float(rt.get("pct_change", 0)) if rt else 0.0
        amt = float(rt.get("amount", 0)) if rt else 0.0

        for sig in signals:
            if _alert_timing(sig.signal_id, user_config) != "post_close":
                continue
            by_signal[sig.signal_name].append({
                "code": code, "name": stock["name"],
                "price": price, "pct": pct, "amount": amt,
                "detail": sig.detail,
                "direction": sig.direction,
            })

    total = sum(len(v) for v in by_signal.values())
    return by_signal, total


def build_post_close_section(by_signal: dict, total: int) -> str:
    """把盘后信号拼成嵌入收盘汇总的小节; 无命中返回空串(空小节省略)。"""
    if total == 0:
        return ""
    lines = [f"■ 盘后信号 (共{total}条·{len(by_signal)}类)", ""]
    for sig_name in sorted(by_signal.keys()):
        items = by_signal[sig_name]
        arrow = "▼" if items[0]["direction"] == "sell" else "▲"
        lines.append(f"━ {arrow} {sig_name} ({len(items)}只) ━")
        for it in items[:15]:  # 单类最多列 15 只, 避免消息过长
            pct_str = f"+{it['pct']:.2f}%" if it["pct"] >= 0 else f"{it['pct']:.2f}%"
            amt_str = f" 成交{_fmt_amount(it['amount'])}" if it["amount"] > 0 else ""
            lines.append(f"• {it['name']}({it['code']}) {it['price']:.2f} {pct_str}{amt_str}")
        if len(items) > 15:
            lines.append(f"  ... 还有 {len(items)-15} 只")
        lines.append("")
    return "\n".join(lines).rstrip()


async def run_post_close_summary():
    """收盘统一汇总 (15:05): 把收盘日报正文 + 弱势极限收盘候选 + 盘后信号合并成一条推送。

    v1.7.x: 原先收盘后会推 3 条(15:00 盘面日报 + 15:00 弱势极限快照 + 15:05 盘后汇总),
    现合并为一条。日报正文复用 15:00 已入库报告的结构化数据; 两类信号小节无命中则省略。
    """
    if not _is_workday():
        logger.info("[post_close] 非工作日,跳过")
        return

    # 1) 收盘日报正文(复用 15:00 入库报告的 market_data)
    context = await repository.get_report_context("1500")
    if context is None:
        logger.warning("[close_summary] 未取到 15:00 报告数据, 仅推信号小节")

    # 2) 弱势极限收盘候选 + 盘后信号 两个小节(无命中省略)
    try:
        we_hits = await collect_weak_extreme_hits()
    except Exception as e:
        logger.warning(f"[close_summary] 弱势极限收集失败: {e}")
        we_hits = []
    try:
        by_signal, total = await collect_post_close_signals()
    except Exception as e:
        logger.warning(f"[close_summary] 盘后信号收集失败: {e}")
        by_signal, total = {}, 0

    # v1.7.397: 弱势极限无命中时收盘汇总带一句确认(替代原14:45的空命中单独推送)
    we_section = build_weak_extreme_section(we_hits) or "■ 弱势极限: 今日无命中"
    sections = [s for s in (we_section,
                            build_post_close_section(by_signal, total)) if s]
    extra = "\n\n━━━━━━━━━━━━━━━\n\n".join(sections)

    # 3) 合并成一条推送(标题: 盘面日报 · 收盘总结)
    sent = await notifier.send_market_report("", "收盘总结", context, extra_sections=extra)
    logger.info(f"[close_summary] 统一收盘推送={sent} 弱势极限{len(we_hits)}只 盘后信号{total}条")
