# -*- coding: utf-8 -*-
"""个股买卖点推送卡片用的「所属板块最近情况」简报。

给定个股 code, 取其 concepts(自选股池里存的概念串) → 用 concept_buckets 归大类,
再到 theme_heat(题材热度=每日各题材涨停家数) 按大类聚合, 算出该股所属最热板块的
近几日涨停家数序列 + 当前状态(启动/升温/高潮/退潮/冷)。纯读, 失败静默返回 None。
"""
from backend.models.repo._db import _fetchone
from backend.models.repo import theme_heat as heat_repo
from backend.services import concept_buckets

# 非题材噪音概念(不参与板块判定)
_NOISE = {"趋势股", "题材股", "科技风格", "次新股", "ST股"}


def _classify_state(seq: list[int], peak: int) -> tuple[str, str]:
    """按近几日涨停家数序列定状态。seq 末位=最近交易日。"""
    today = seq[-1] if seq else 0
    first = seq[0] if seq else 0
    if today >= 8:
        return "高潮", "🔥"
    if today <= 1 and peak <= 2:
        return "冷", "❄️"
    if peak >= 4 and today <= peak * 0.5:
        return "退潮", "📉"
    if today >= 3 and today > first:
        return ("启动", "🚀") if first <= 1 else ("升温", "🔥")
    if today >= 3:
        return "活跃", "🔥"
    return "平淡", "➖"


async def get_sector_brief(code: str, user_id: int | None) -> dict | None:
    """返回 {label, status, emoji, today, seq(近3日), trend} 或 None。"""
    if not code:
        return None
    row = await _fetchone(
        "SELECT concepts FROM cfzy_biz_stock_pool WHERE code=%s AND user_id=%s",
        (code, user_id or 1))
    concepts = (row or {}).get("concepts") or ""
    cs = [c.strip() for c in concepts.split(",") if c.strip() and c.strip() not in _NOISE]
    if not cs:
        return None

    rows = await heat_repo.get_theme_heat(days=5)
    if not rows:
        return None
    dates = sorted({str(r["trade_date"])[:10] for r in rows})
    if not dates:
        return None
    today = dates[-1]

    # 题材热度按「大类桶」与「细题材名」两套聚合(同日累加涨停家数)
    bucket_by_day: dict[str, dict[str, int]] = {}
    theme_by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        d = str(r["trade_date"])[:10]
        th = r["theme"]
        cnt = int(r.get("limit_up_count") or 0)
        theme_by_day.setdefault(th, {})
        theme_by_day[th][d] = theme_by_day[th].get(d, 0) + cnt
        bk = concept_buckets.classify(th)
        if bk and bk != concept_buckets.OTHER:
            bucket_by_day.setdefault(bk, {})
            bucket_by_day[bk][d] = bucket_by_day[bk].get(d, 0) + cnt

    # 候选板块 = 个股概念映射到的大类桶(优先) + 直接命中的细题材名
    cands: list[tuple[str, dict[str, int]]] = []
    seen = set()
    for c in cs:
        bk = concept_buckets.classify(c)
        if bk and bk != concept_buckets.OTHER and bk not in seen and bk in bucket_by_day:
            cands.append((bk, bucket_by_day[bk]))
            seen.add(bk)
    for c in cs:
        if c in theme_by_day and c not in seen:
            cands.append((c, theme_by_day[c]))
            seen.add(c)
    if not cands:
        return None

    # 选今日涨停最多的候选; 全 0 则选近5日累计最多
    cands.sort(key=lambda x: (x[1].get(today, 0), sum(x[1].values())), reverse=True)
    label, series = cands[0]
    if sum(series.values()) == 0:
        return None

    last3 = dates[-3:]
    seq = [series.get(d, 0) for d in last3]
    tcnt = series.get(today, 0)
    peak = max(series.values())
    status, emoji = _classify_state(seq, peak)
    trend = "走强" if len(seq) >= 2 and seq[-1] > seq[0] else (
        "走弱" if len(seq) >= 2 and seq[-1] < seq[0] else "持平")
    return {"label": label, "status": status, "emoji": emoji,
            "today": tcnt, "seq": seq, "trend": trend}
