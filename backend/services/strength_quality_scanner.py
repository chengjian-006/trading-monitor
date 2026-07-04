"""真假强势评分快照任务(SCORE_STRENGTH v2)

调度时点(cron):
    14:30  尾盘前一刻,基于全天 90% 成交量已成,评分稳定

逻辑:
    1. 拉沪指近 N 日数据(用于抗跌判定 + G/H 维度)
    2. 拉板块涨幅榜(用于主流题材 + I 维度)
    3. 扫所有 focused/hold 自选股(排除 ST)
       对满足"近 5 日跑赢大盘 ≥ N 日"的票计算评分
       高分票(≥20)进一步拉笔级大单数据精算
    4. 排序 → 推送一条汇总企业微信
"""
import asyncio
import logging
from datetime import datetime

from backend.models import repository
from backend import data_fetcher
from backend.services import signal_engine, notifier
from backend.services.lark_notifier import md_element as _md, md_table as _table
from backend.services.trading_concepts import (
    compute_strength_quality,
    strength_quality_config_from_dict,
    detect_mainstream_theme,
    mainstream_theme_config_from_dict,
)

logger = logging.getLogger(__name__)


from backend.core.trading_calendar import is_workday as _is_workday  # v1.7.x 统一来源


def _is_st(stock: dict) -> bool:
    return "ST" in (stock.get("name") or "").upper().strip()


from backend.utils.formatting import fmt_pct as _fmt_pct  # 统一百分比格式化


async def _market_data(days: int = 5) -> dict | None:
    try:
        sh = await data_fetcher.get_daily_kline("000001", days=days + 15)
    except Exception as e:
        logger.warning(f"[strength_quality] 拉沪指失败: {e}")
        return None
    if sh is None or sh.empty or len(sh) < days + 1:
        return None
    pcts = []
    for i in range(-days, 0):
        prev_c = float(sh.iloc[i - 1]["close"])
        cur_c = float(sh.iloc[i]["close"])
        if prev_c > 0:
            pcts.append((cur_c - prev_c) / prev_c * 100)
    today_close = float(sh.iloc[-1]["close"])
    prev_5d_close = float(sh.iloc[-6]["close"]) if len(sh) >= 6 else 0
    cum_pct = (today_close - prev_5d_close) / prev_5d_close * 100 if prev_5d_close > 0 else 0
    n10_low = float(sh.iloc[-10:]["close"].min()) if len(sh) >= 10 else today_close
    return {
        "pcts": pcts, "cum_pct": cum_pct,
        "today_close": today_close, "n10_low": n10_low,
    }


def _count_outperform(stock_df, market_pcts: list[float], days: int = 5) -> int:
    if len(stock_df) < days + 1 or len(market_pcts) < days:
        return 0
    cnt = 0
    for i in range(days):
        s_prev = float(stock_df.iloc[-(i + 2)]["close"])
        s_cur = float(stock_df.iloc[-(i + 1)]["close"])
        s_pct = (s_cur - s_prev) / s_prev * 100 if s_prev > 0 else 0
        m_pct = market_pcts[-(i + 1)]
        if s_pct > m_pct:
            cnt += 1
    return cnt


async def scan_strength_quality_snapshot(return_only: bool = False):
    """定时入口:扫股票池 → 评分 → 企微推送汇总。

    return_only=True 时只返回 (企微文本, 飞书elements) 不推送(供 14:40 尾盘决策合并卡复用);
    无数据/跳过时返回 None。
    """
    if not _is_workday():
        logger.info("[strength_quality] 非工作日,跳过")
        return

    # 1. 配置
    user_config = await repository.get_signal_config(1)
    merged = signal_engine.get_merged_config(user_config)
    sq_cfg = strength_quality_config_from_dict(merged.get("SCORE_STRENGTH", {}))
    mt_cfg = mainstream_theme_config_from_dict(merged.get("SCORE_THEME", {}))
    if not merged.get("SCORE_STRENGTH", {}).get("enabled", True):
        logger.info("[strength_quality] 信号已禁用,跳过")
        return

    # 2. 大盘
    market = await _market_data(5)
    if not market:
        logger.warning("[strength_quality] 大盘数据失败,跳过")
        return
    market_pcts = market["pcts"]
    market_cum_pct = market["cum_pct"]
    market_today = market["today_close"]
    market_n10_low = market["n10_low"]

    # 3. 板块榜
    try:
        ranking = await data_fetcher.get_sector_ranking(top_n=30)
    except Exception:
        ranking = []

    # 4. 自选股(focused 或 hold,排除 ST,按 code 去重)
    all_stocks = await repository.list_all_stocks()
    by_code: dict[str, dict] = {}
    for s in all_stocks:
        if not (s.get("focused") or s.get("status") == "hold"):
            continue
        if _is_st(s):
            continue
        by_code.setdefault(s["code"], s)

    if not by_code:
        logger.info("[strength_quality] 自选股为空,跳过")
        return

    # 5. 并发评分
    sem = asyncio.Semaphore(3)

    async def _evaluate(code: str, stock: dict):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=60)
            except Exception:
                return None
            if df is None or df.empty or len(df) < 10:
                return None

            ind = signal_engine.compute_indicators(df)
            outperform = _count_outperform(ind, market_pcts, days=5)
            if outperform < sq_cfg.min_persist_days:
                return None

            industry = stock.get("industry", "")
            overview = None
            is_mainstream = False
            rank_in_sector = None
            if industry:
                try:
                    overview = await data_fetcher.get_sector_overview(industry, top_n=30)
                    if overview:
                        mt = detect_mainstream_theme(overview, ranking, mt_cfg)
                        is_mainstream = mt.get("is_mainstream", False)
                        for i, s in enumerate(overview.get("top_stocks", []) or [], 1):
                            if s.get("code") == code:
                                rank_in_sector = i
                                break
                except Exception:
                    pass

            stock_today_close = float(ind.iloc[-1]["close"])
            n10 = ind.iloc[-10:] if len(ind) >= 10 else ind
            stock_n10_high = float(n10["close"].max())
            prev_5d_close = float(ind.iloc[-6]["close"]) if len(ind) >= 6 else 0
            stock_5d_cum = (stock_today_close - prev_5d_close) / prev_5d_close * 100 if prev_5d_close > 0 else 0

            extras = {
                "market_5d_cum_pct": market_cum_pct,
                "stock_5d_cum_pct": stock_5d_cum,
                "market_today_vs_10d_low": market_today / market_n10_low if market_n10_low > 0 else 1.0,
                "stock_today_vs_10d_high": stock_today_close / stock_n10_high if stock_n10_high > 0 else 1.0,
                "rank_in_sector": rank_in_sector,
            }

            preliminary = compute_strength_quality(
                ind, outperform, None, overview, is_mainstream, **extras, cfg=sq_cfg
            )
            big_orders = None
            if preliminary["score"] >= 20:
                try:
                    big_orders = await data_fetcher.get_big_orders_today(code, 15_000_000)
                except Exception:
                    pass

            result = compute_strength_quality(
                ind, outperform, big_orders, overview, is_mainstream, **extras, cfg=sq_cfg
            )

            return {
                "code": code,
                "name": stock["name"],
                "industry": industry,
                "close": stock_today_close,
                "stock_5d_cum": stock_5d_cum,
                "score": result["score"],
                "grade": result["grade"],
                "is_real_strong": result["is_real_strong"],
                "criteria": result["criteria"],
            }

    results = await asyncio.gather(*[_evaluate(c, s) for c, s in by_code.items()])
    results = [r for r in results if r is not None]

    real_strong = sorted(
        [r for r in results if r["is_real_strong"]],
        key=lambda x: x["score"], reverse=True,
    )
    observe = sorted(
        [r for r in results if not r["is_real_strong"] and r["score"] >= sq_cfg.observe_threshold],
        key=lambda x: x["score"], reverse=True,
    )[:8]  # 观望最多展示 8 只

    # 6. 拼推送(飞书结构化表格卡 + 企微纯文本兜底)
    head = (f"沪指 {market_today:.2f}　·　5日 {_fmt_pct(market_cum_pct)}　|　"
            f"🟢真强势 {len(real_strong)}　·　🟡观望 {len(observe)}")
    GUIDE = "💡 真强势=大盘企稳首日率先放量上攻者, 才是买点; 观望=强度够但未确认, 等放量再跟"

    if not real_strong and not observe:
        text = (
            f"【真假强势评分快照·14:30】\n\n{head}\n\n"
            f"当前股票池无真强势/观望候选\n"
            f"(评分对象 {len(results)} 只,均 < {sq_cfg.observe_threshold} 分)"
        )
        elements = [
            _md("**📊 真假强势评分快照**　_14:30 盘中_"),
            _md(head),
            _md(f"_当前股票池无真强势/观望候选(评分 {len(results)} 只均 < {sq_cfg.observe_threshold} 分)_"),
        ]
    else:
        REAL_CAP = 15  # 真强势封顶, 多余只标计数, 不无限堆
        shown_real = real_strong[:REAL_CAP]

        def _plus(crit):
            top = sorted(crit, key=lambda c: c["delta"], reverse=True)[:2]
            return " ".join(f"+{c['delta']}{c['name']}" for c in top)

        elements = [
            _md("**📊 真假强势评分快照**　_14:30 盘中_"),
            _md(head),
        ]
        # ── 真强势表(移动优化: 评分独占前置短列, 个股/5日/加分并进名称格) ──
        if real_strong:
            more = f"，展示前 {REAL_CAP}" if len(real_strong) > REAL_CAP else ""
            elements.append(_md(f"🟢 **真强势**（{len(real_strong)} 只{more}）"))
            def _real_info(r):
                s = f"{r['name']} {r['code']}　5日{_fmt_pct(r['stock_5d_cum'])}"
                plus = _plus(r["criteria"])
                return s + (f"　{plus}" if plus else "")
            elements.append(_table(
                [{"name": "score", "display_name": "评分"},
                 {"name": "info", "display_name": "个股 · 5日 · 加分"}],
                [{"score": f"{r['score']}分", "info": _real_info(r)} for r in shown_real],
            ))
        # ── 观望表(移动优化: 评分前置, 个股+5日并列) ──
        if observe:
            elements.append(_md(f"🟡 **观望**（展示前 {len(observe)} 只）"))
            elements.append(_table(
                [{"name": "score", "display_name": "评分"},
                 {"name": "info", "display_name": "个股 · 5日"}],
                [{"score": f"{r['score']}分",
                  "info": f"{r['name']} {r['code']}　5日{_fmt_pct(r['stock_5d_cum'])}"}
                 for r in observe],
            ))
        elements.append(_md(GUIDE))

        # 企微纯文本兜底(无原生表格, 压缩成单行/股)
        lines = [f"【真假强势评分快照·14:30】", head, ""]
        if real_strong:
            lines.append(f"🟢 真强势 ({len(real_strong)} 只)")
            for r in shown_real:
                lines.append(
                    f"  • {r['name']}({r['code']}) [{r['industry'] or '—'}]  "
                    f"{r['score']}分  5日{_fmt_pct(r['stock_5d_cum'])}  {_plus(r['criteria'])}"
                )
            if len(real_strong) > REAL_CAP:
                lines.append(f"  …等共 {len(real_strong)} 只")
        if observe:
            lines.append("")
            lines.append(f"🟡 观望 (展示前 {len(observe)} 只)")
            for r in observe:
                lines.append(f"  • {r['name']}({r['code']})  {r['score']}分  5日{_fmt_pct(r['stock_5d_cum'])}")
        lines.append("")
        lines.append(GUIDE)
        text = "\n".join(lines)

    if return_only:
        return text, elements
    sent = await notifier.send_dual_card(text, lark_title="📊 真假强势评分快照", elements=elements)
    logger.info(
        f"[strength_quality] 评分 {len(results)} 只 / 真强势 {len(real_strong)} / 观望 {len(observe)} / 推送结果={sent}"
    )
