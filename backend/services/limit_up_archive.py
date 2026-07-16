"""每日涨停复盘 — 存档 + 收盘推送 (v1.7.572).

数据源: 同花顺涨停池 limit_pool(与远航版同口径), 每只涨停股带 涨停概念(reason)/板数/炸板。
  run_limit_up_daily 15:35 收盘后: 拉当日涨停池 → 存 cfzy_sys_limit_up_pool/daily → 推一张飞书复盘卡。
  backfill_limit_up(days) 回填历史(同花顺 limit_up_pool 支持历史 date 参数)。

推送策略(守推送降噪): 卡里只放精华 —— 数据概览 + 连板梯队 + 热点分布 top, 不堆全部104只;
完整列表看看板页面。
"""
import asyncio
import logging
from collections import Counter
from datetime import datetime

from backend.core.trading_calendar import is_workday
from backend.fetcher.limit_pool import get_limit_pool
from backend.models import repository

logger = logging.getLogger(__name__)

# 概念归一(多标签近义合并到一个主题, 供热点分布计数)
_MERGE = {
    "宇树": "机器人", "减速器": "机器人", "减速机": "机器人", "灵巧手": "机器人",
    "谐波": "机器人", "丝杠": "机器人", "具身智能": "机器人", "人形机器人": "机器人",
    "航天": "商业航天", "卫星": "商业航天", "火箭": "商业航天",
    "存储": "半导体", "芯片": "半导体", "封装": "半导体", "SSD": "半导体",
    "覆铜板": "PCB", "创新药": "医药", "摘帽": "ST摘帽", "脱星": "ST摘帽",
    "算力": "算力/数据中心", "数据中心": "算力/数据中心", "液冷": "算力/数据中心",
}
# 计数用关键词(命中即归入对应主题)
_KEYWORDS = ["机器人", "宇树", "减速器", "减速机", "灵巧手", "谐波", "丝杠", "具身智能",
             "黄金", "半导体", "存储", "芯片", "封装", "商业航天", "航天", "卫星", "PCB",
             "覆铜板", "创新药", "医药", "ST", "摘帽", "算力", "数据中心", "液冷", "特斯拉",
             "低空经济", "光伏", "变压器", "资产重组"]


def build_concept_ranking(boards: list[dict], top: int = 10, min_count: int = 2) -> list[tuple[str, int]]:
    """按概念关键词命中只数排名(归一近义)。返回 [(主题, 只数), ...] 降序。"""
    counter: Counter = Counter()
    for b in boards:
        reason = b.get("reason") or ""
        seen = set()
        for kw in _KEYWORDS:
            if kw in reason:
                seen.add(_MERGE.get(kw, kw))
        for theme in seen:
            counter[theme] += 1
    return [(t, c) for t, c in counter.most_common(top) if c >= min_count]


def build_review_text(trade_date: str, meta: dict, boards: list[dict], link: str = "") -> str:
    """飞书/微信复盘卡正文(精华版): 概览 + 连板梯队 + 热点分布 top。"""
    d = trade_date
    ymd = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
    seal = meta.get("seal_rate")
    seal_s = f"{seal*100:.0f}%" if isinstance(seal, (int, float)) else "—"
    lines = [
        f"📊 涨停复盘 · {ymd}",
        "",
        f"涨停 **{meta.get('limit_up_count', '—')}** 家　曾涨停 {meta.get('limit_up_history', '—')}　"
        f"炸板 {meta.get('broken_board_count', '—')}　封板率 {seal_s}　跌停 {meta.get('limit_down_count', '—')}",
    ]
    ladder = [b for b in boards if (b.get("height") or 1) >= 2]
    ladder.sort(key=lambda x: (-(x.get("height") or 1), x.get("code", "")))
    if ladder:
        lines += ["", f"**连板梯队**（{len(ladder)}只）"]
        for b in ladder[:20]:
            concept = (b.get("reason") or "").split("+")[0]
            lines.append(f"{b.get('streak_label', '')}　{b.get('name', '')}({b.get('code', '')})　{concept}")
    ranking = build_concept_ranking(boards)
    if ranking:
        lines += ["", "**热点分布**　" + "　".join(f"{t}{c}" for t, c in ranking)]
    if link:
        lines += ["", f"[👉 查看全部涨停复盘]({link})"]
    return "\n".join(lines)


def build_review_card(trade_date: str, meta: dict, boards: list[dict], link: str = ""):
    """涨停复盘 → 基线 v1.1 结构卡(情报族 blue, 它是复盘情报非机会):
    KPI结论区 → 连板梯队短表(>8行 Top5+全量折叠) → 热点分布 → 👉一句话定性 → 折叠。"""
    from backend.services import card_kit
    from backend.services.lark_notifier import md_element

    d = trade_date
    ymd = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else d
    seal = meta.get("seal_rate")
    seal_s = f"{seal*100:.0f}%" if isinstance(seal, (int, float)) else "—"
    n_up = meta.get("limit_up_count", "—")
    n_down = meta.get("limit_down_count", "—")

    ladder = [b for b in boards if (b.get("height") or 1) >= 2]
    ladder.sort(key=lambda x: (-(x.get("height") or 1), x.get("code", "")))
    ranking = build_concept_ranking(boards)
    top_theme = ranking[0][0] if ranking else ""

    elements: list = [card_kit.kpi_row([
        ("涨停", f"{n_up}家", "red"),
        ("封板率", seal_s),
        ("跌停", f"{n_down}家", "green" if n_down not in ("—", 0, None) else None),
    ])]
    elements.append(md_element(
        f"曾涨停 {meta.get('limit_up_history', '—')} · 炸板 {meta.get('broken_board_count', '—')}"))

    def _row(b) -> tuple:
        concept = (b.get("reason") or "").split("+")[0]   # 复盘卡刻意用首段短标签(勿改全标签口径)
        return (b.get("name", ""), b.get("streak_label", ""), concept)

    fold_detail = ""
    if ladder:
        elements.append(md_element(f"**连板梯队**（{len(ladder)}只）"))
        if len(ladder) > 8:
            elements.append(card_kit.short_table(["股票", "板数", "题材"], [_row(b) for b in ladder[:5]]))
            elements.append(md_element(f"…等 **{len(ladder)}** 只，全量见折叠"))
            fold_detail = "\n".join(
                f"{b.get('streak_label', '')}　**{b.get('name', '')}**({b.get('code', '')})　{_row(b)[2]}"
                for b in ladder)
        else:
            elements.append(card_kit.short_table(["股票", "板数", "题材"], [_row(b) for b in ladder]))
    if ranking:
        elements.append(md_element("**热点分布**　" + "　".join(f"{t}{c}" for t, c in ranking)))

    if ladder:
        advice_text = f"{ladder[0].get('streak_label', '')}领衔，热点看{top_theme}" if top_theme \
            else f"{ladder[0].get('streak_label', '')}领衔，看高度能否延续"
    else:
        advice_text = "无连板，打板情绪弱，谨慎追高"
    elements.append(card_kit.advice(advice_text[:24]))
    if fold_detail:
        elements.append(card_kit.fold(f"连板梯队全量（{len(ladder)}只）", fold_detail))

    return card_kit.Card(
        title="📊 涨停复盘", elements=elements,
        fallback=build_review_text(trade_date, meta, boards, link),
        family="intel",
        summary=card_kit.summary_text("涨停复盘", f"涨停{n_up}家", f"封板率{seal_s}",
                                      f"最高{ladder[0].get('streak_label', '')}" if ladder else ""),
        subtitle=f"{ymd} 收盘复盘",
        link_url=link, link_text="查看全部涨停复盘")


async def archive_limit_up(date: str) -> int:
    """拉某交易日涨停池并存档。date: 'YYYYMMDD'。返回明细行数(0=无数据)。"""
    pool = await get_limit_pool(date)
    if not pool or not pool.get("boards"):
        logger.info(f"[limit_up] {date} 涨停池无数据, 跳过存档")
        return 0
    meta = {k: pool.get(k) for k in
            ("limit_up_count", "limit_up_history", "limit_down_count", "broken_board_count", "seal_rate")}
    n = await repository.upsert_limit_up_daily(date, meta, pool["boards"])
    logger.info(f"[limit_up] {date} 存档 {n} 只涨停(涨停{meta.get('limit_up_count')}家)")
    return n


async def backfill_limit_up(days: int = 45) -> dict:
    """回填最近 N 个自然日里的交易日涨停池(同花顺支持历史 date)。返回 {ok, empty}。"""
    from datetime import timedelta
    today = datetime.now()
    ok, empty = 0, 0
    for i in range(days):
        d = today - timedelta(days=i)
        if not is_workday(d):
            continue
        ds = d.strftime("%Y%m%d")
        try:
            if await archive_limit_up(ds) > 0:
                ok += 1
            else:
                empty += 1
        except Exception as e:
            logger.warning(f"[limit_up] 回填 {ds} 失败: {e}")
            empty += 1
        await asyncio.sleep(0.3)   # 轻微限速, 别撞同花顺反爬
    logger.info(f"[limit_up] 回填完成: {ok} 天有数据 / {empty} 天空")
    return {"ok": ok, "empty": empty}


async def run_limit_up_daily():
    """15:35 收盘后: 存档当日涨停池 + 推一张飞书复盘卡。"""
    now = datetime.now()
    if not is_workday(now):
        return
    date = now.strftime("%Y%m%d")
    n = await archive_limit_up(date)
    if n <= 0:
        logger.info("[limit_up] 当日无涨停数据, 不推送")
        return
    got = await repository.get_limit_up_daily(date)
    if not got:
        return
    try:
        from backend.core.config import load_config
        base = (load_config().get("site_base_url") or "").rstrip("/")
        link = f"{base}/limit-up" if base else ""
        card = build_review_card(date, got["meta"], got["boards"], link)
        from backend.services import notifier
        await notifier.send_card(card)
        logger.info(f"[limit_up] {date} 复盘卡已推送")
    except Exception as e:
        logger.warning(f"[limit_up] 复盘推送失败: {e}")
