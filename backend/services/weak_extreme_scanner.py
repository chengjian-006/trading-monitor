"""S0 弱势极限定时快照任务。

调度时点(cron) 与推送分工:
    11:30  上午收盘 — 并入盘面播报(run_market_report), 不单独推
    14:45  尾盘 — 单独推一条供盘中决策(盘未收, 用分时外推量, 与 11:30 同口径)
    15:05  收盘汇总 — run_post_close_summary 仍带弱势极限小节(真实全天量复核)

逻辑:
    扫所有用户的全池自选股,检测每只票是否命中 BUY_WEAK_EXTREME,
    把命中清单汇总成一条企业微信消息推送(跟 scanner 实时单条推送互补)。
"""
import asyncio
import logging
from datetime import datetime

from backend.models import repository
from backend import data_fetcher
from backend.services import signal_engine, notifier

logger = logging.getLogger(__name__)


def _slot_label() -> str:
    """根据当前时间返回时段标签(用于消息标题)。
    v1.7.398: 标签不再带具体时间(推送已统一带发送时间, 标题里的时间是重复信息)。"""
    h = datetime.now().hour
    if h == 11:
        return "上午收盘快照"
    if h == 14:
        return "尾盘快照"
    if h == 15:
        return "收盘快照"
    return "快照"


from backend.utils.formatting import fmt_amount as _fmt_amount  # 统一格式化


def _format_hit_line(h: dict) -> str:
    """单只命中票格式化为一行紧凑文本。"""
    pct = h.get("pct", 0)
    pct_str = f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"
    amt_str = f"  成交 {_fmt_amount(h.get('amount', 0))}" if h.get("amount") else ""
    return f"• {h['name']}({h['code']})  {h['close']:.2f} {pct_str}{amt_str}\n  {h['detail']}"


from backend.core.trading_calendar import is_workday as _is_workday  # 统一交易日判断


async def collect_weak_extreme_hits() -> list[dict]:
    """扫股票池(v1.7.589: 在池即扫不再要求关注, 排除 ST/概念指数), 返回命中 BUY_WEAK_EXTREME 的票列表。纯收集, 不推送。"""
    all_stocks = await repository.list_all_stocks()
    by_code: dict[str, dict] = {}
    for s in all_stocks:
        if s.get("trade_type") == "index":
            continue
        name = (s.get("name") or "").upper().strip()
        if "ST" in name:  # v1.7.16: 过滤 ST/*ST
            continue
        by_code.setdefault(s["code"], s)
    if not by_code:
        logger.info("[weak_extreme_snapshot] 股票池为空,跳过")
        return []

    codes = list(by_code.keys())
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[weak_extreme_snapshot] 拉行情失败: {e}")
        quotes = {}

    sem = asyncio.Semaphore(3)

    async def _fetch_kline(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=120)
            except Exception as e:
                logger.warning(f"[weak_extreme_snapshot] {code} K线失败: {e}")
                df = None
            await asyncio.sleep(0.3)
            return code, df

    kline_results = await asyncio.gather(*[_fetch_kline(c) for c in codes])
    kline_map = {code: df for code, df in kline_results}

    # 用 admin (user_id=1) 的配置作为基准(快照任务跨用户共享一份汇总)
    user_config = await repository.get_signal_config(1)

    we_hits: list[dict] = []  # S0 弱势极限
    for code, stock in by_code.items():
        df = kline_map.get(code)
        if df is None or df.empty or len(df) < 20:
            continue
        rt = quotes.get(code)
        try:
            signals = signal_engine.detect_signals(df, "short", rt, user_config)
        except Exception as e:
            logger.warning(f"[weak_extreme_snapshot] {code} 检测失败: {e}")
            continue

        price = rt["price"] if rt and rt.get("price") else float(df.iloc[-1]["close"])
        pct = float(rt.get("pct_change", 0)) if rt else 0.0
        amt = float(rt.get("amount", 0)) if rt else 0.0
        for sig in signals:
            if sig.signal_id == "BUY_WEAK_EXTREME":
                we_hits.append({
                    "code": code, "name": stock["name"],
                    "close": price, "pct": pct, "amount": amt, "detail": sig.detail,
                })
    return we_hits


def build_weak_extreme_section(we_hits: list[dict]) -> str:
    """把弱势极限命中拼成嵌入收盘汇总的小节; 无命中返回空串(空小节省略)。"""
    if not we_hits:
        return ""
    we_lines = "\n\n".join(_format_hit_line(h) for h in we_hits)
    return f"■ 弱势极限·收盘候选 ({len(we_hits)}只)\n\n{we_lines}"


_WEAK_ADVICE = "等收盘确认再入场，左侧耐心持有"


def build_weak_extreme_card(we_hits: list[dict], slot: str = "尾盘快照"):
    """基线 v1.1 结构卡: 机会家族(买点候选=红, 原 blue 升级)。五区骨架:
    结论行 → 全短列表(股票|现价|涨跌, 长值成交额/技术参数下沉折叠) → 👉建议 → 折叠技术参数。
    返回 card_kit.Card, 经 notifier.send_card 发送; fallback 纯文本保 PushPlus 同源信息量。"""
    from backend.services import card_kit
    from backend.services.lark_notifier import md_element
    n = len(we_hits)
    first = we_hits[0]
    if n == 1:
        title = f"📈 弱势极限 · {first['name']}({first['code']})"
        concl = (f"**{first['name']}({first['code']})** 触发弱势极限地量，"
                 f"现价 **{first['close']:.2f}**（{card_kit.pct_md(first.get('pct', 0))}）")
        summary = card_kit.summary_text(
            first["name"], first["code"], "弱势极限",
            f"{first['close']:.2f}", f"{first.get('pct', 0):+.2f}%")
    else:
        title = f"📈 弱势极限 · {n}只"
        concl = f"自选池 **{n}只** 触发弱势极限地量（缩量贴线·收盘候选）"
        summary = card_kit.summary_text(f"{n}只", "弱势极限收盘候选")
    elements = [md_element(concl)]
    if n > 1:  # 单只时结论行已含全部短值, 免一行表
        rows = [(f"{h['name']}({h['code']})", f"{h['close']:.2f}",
                 card_kit.pct_md(h.get("pct", 0), bold=False)) for h in we_hits]
        elements.append(card_kit.short_table(["股票", "现价", "涨跌"], rows))
    elements.append(card_kit.advice(_WEAK_ADVICE))
    detail_lines = []
    for h in we_hits:
        amt_str = f"　成交 {_fmt_amount(h.get('amount', 0))}" if h.get("amount") else ""
        detail_lines.append(f"**{h['name']}({h['code']})**{amt_str}\n{h['detail']}")
    if detail_lines:
        elements.append(card_kit.fold("技术参数（点击展开）", "\n\n".join(detail_lines)))
    fallback = (f"【弱势极限·{slot}】{n}只候选\n\n"
                + "\n\n".join(_format_hit_line(h) for h in we_hits)
                + f"\n\n👉 {_WEAK_ADVICE}")
    return card_kit.Card(
        title=title, elements=elements, fallback=fallback,
        family="opportunity", summary=summary,
        subtitle="收盘入场 · 左侧", tags=[("买点候选", "red")],
    )


async def scan_weak_extreme_snapshot():
    """定时快照入口: 扫股票池 → 检测 S0 → 汇总企微推送。

    推送分工(v1.7.345):
      11:30 并入盘面播报(run_market_report), 此处跳过;
      14:45 尾盘单独推一条(供盘中决策, 盘未收用分时外推量), 走本函数下半段;
      15:05 收盘统一汇总(run_post_close_summary)仍带弱势极限小节复核。
    故此处遇 11 点 / 15 点跳过, 14 点(14:45)正常单独推。
    """
    if not _is_workday():
        logger.info("[weak_extreme_snapshot] 非工作日,跳过")
        return
    h = datetime.now().hour
    if h == 15:
        logger.info("[weak_extreme_snapshot] 15:00 收盘并入 15:05 统一汇总, 跳过单独推送")
        return
    if h == 11:
        logger.info("[weak_extreme_snapshot] 11:30 上午收盘并入盘面播报, 跳过单独推送")
        return

    we_hits = await collect_weak_extreme_hits()
    slot = _slot_label()
    if not we_hits:
        logger.info(f"[weak_extreme_snapshot] {slot} 无命中, 不推送(收盘汇总会带确认)")
        return
    # 基线 v1.1: 结构卡(买点候选=机会家族红, 原 blue), 摘要/副标题/彩签走 send_card 信封
    card = build_weak_extreme_card(we_hits, slot)
    sent = await notifier.send_card(card)
    logger.info(f"[weak_extreme_snapshot] {slot} 弱势极限{len(we_hits)}只 推送={sent}")
