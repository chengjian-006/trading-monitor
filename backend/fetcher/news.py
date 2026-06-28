"""个股公告 + 资讯 + 人气榜全报告 - v1.7.x.

  get_stock_announcements  — 重要公告(异动/收购/合同/业绩等关键词过滤)
  get_stock_news           — 个股资讯标题(每只 2 条)
  get_popularity_full      — 人气榜 + 个股扩展数据 + 热门概念/行业 (聚合 5 个外部接口)
"""
import asyncio
import json
import logging

from backend.fetcher.codes import _code_to_em
from backend.fetcher.http_client import HEADERS, _get_client

logger = logging.getLogger(__name__)


async def get_stock_announcements(codes: list[str]) -> dict[str, list[str]]:
    """关键词过滤的重要公告 (异动/重大/收购/合同/中标/业绩/增持/回购/预增/预盈), 3 次重试."""
    code_list = ",".join(codes)
    url = (f"https://np-anotice-stock.eastmoney.com/api/security/ann"
           f"?cb=cb&sr=-1&page_size=50&page_index=1"
           f"&ann_type=SHA,SZA&client_source=web&stock_list={code_list}")
    keywords = ("异动", "风险", "重大", "收购", "合同", "中标", "业绩", "增持", "回购", "预增", "预盈")
    client = _get_client()

    for attempt in range(1, 4):
        try:
            resp = await client.get(url, headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer": "https://data.eastmoney.com",
            })
            text = resp.text
            start = text.find("(")
            end = text.rfind(")")
            if start < 0 or end <= start:
                if attempt < 3:
                    logger.warning(f"[announcements] 第{attempt}次解析失败, 2s 后重试")
                    await asyncio.sleep(2)
                continue
            data = json.loads(text[start + 1:end])
            result_map: dict[str, list[str]] = {c: [] for c in codes}
            for item in data.get("data", {}).get("list", []):
                title = item.get("title", "")
                if not any(kw in title for kw in keywords):
                    continue
                date = (item.get("notice_date", "") or "")[:10]
                for sc in item.get("codes", []):
                    code = sc.get("stock_code", "")
                    if code in result_map and len(result_map[code]) < 2:
                        result_map[code].append(f"{date} {title}")
            return result_map
        except Exception as e:
            logger.warning(f"[announcements] 第{attempt}次失败: {e}")
            if attempt < 3:
                await asyncio.sleep(2)

    logger.error("Announcements fetch failed after 3 attempts")
    return {c: [] for c in codes}


async def get_stock_news(codes: list[str]) -> dict[str, list[str]]:
    """个股资讯标题, 每只 2 条 (东财 wap 资讯接口)."""
    result: dict[str, list[str]] = {c: [] for c in codes}
    client = _get_client()
    sem = asyncio.Semaphore(5)

    async def fetch_one(code: str):
        secid = _code_to_em(code)
        url = (f"https://np-listapi.eastmoney.com/comm/wap/getListInfo"
               f"?cb=&client=wap&type=1&mTypeAndCode={secid}"
               f"&pageSize=2&pageNo=1&fields=title,showDateTime")
        async with sem:
            for attempt in range(1, 3):
                try:
                    resp = await client.get(url, headers={
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": "https://wap.eastmoney.com",
                    })
                    data = resp.json()
                    items = data.get("data", {}).get("list", []) if data.get("data") else []
                    result[code] = [item.get("Art_Title", "") for item in items
                                    if item.get("Art_Title")][:2]
                    return
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(1)

    await asyncio.gather(*[fetch_one(c) for c in codes])
    return result


async def get_popularity_full(top_n: int = 20) -> dict:
    """人气榜聚合报告: 个股扩展数据 + 热门概念/行业. 聚合 5 个外部接口的结果."""
    # 延迟 import 避免循环
    from backend.fetcher.popularity import get_popularity_rank
    from backend.fetcher.quotes import get_realtime_quotes
    from backend.fetcher.stock_extra import get_stock_extra
    from backend.fetcher.sectors import get_stock_concepts, get_concept_板块_quotes

    rank_list = await get_popularity_rank(top_n)
    if not rank_list:
        return {"stocks": [], "hot_concepts": []}

    codes = [r["code"] for r in rank_list]
    rank_map = {r["code"]: r for r in rank_list}

    quotes, extras, (concepts_map, bk_code_map), anns_map, news_map = await asyncio.gather(
        get_realtime_quotes(codes),
        get_stock_extra(codes),
        get_stock_concepts(codes),
        get_stock_announcements(codes),
        get_stock_news(codes),
    )

    stocks = []
    concept_counter: dict[str, list[str]] = {}
    for code in codes:
        q = quotes.get(code, {})
        e = extras.get(code, {})
        r = rank_map[code]
        concepts = concepts_map.get(code, [])
        name = q.get("name", "")
        news = news_map.get(code, [])
        anns = anns_map.get(code, [])
        hot_reason = (news + anns)[:3]

        display_name = name or code
        for c in concepts:
            concept_counter.setdefault(c, [])
            if display_name not in concept_counter[c]:
                concept_counter[c].append(display_name)

        stocks.append({
            "rank": r["rank"],
            "rank_change": r.get("rank_change", 0),
            "code": code,
            "name": name,
            "pct_change": q.get("pct_change", 0),
            "amount": q.get("amount", 0),
            "turnover": e.get("turnover", 0),
            "speed": e.get("speed", 0),
            "industry": e.get("industry", ""),
            "concepts": concepts,
            "announcements": anns,
            "news": news,
            "hot_reason": hot_reason,
            "ai_analysis": "",
        })

    industry_counter: dict[str, list[str]] = {}
    for s in stocks:
        ind = s.get("industry", "")
        if ind:
            display = s["name"] or s["code"]
            industry_counter.setdefault(ind, [])
            if display not in industry_counter[ind]:
                industry_counter[ind].append(display)

    # 涨停分布: 只统计今天涨停(≥9.5%)的票, 板块没有涨停票的不算热门
    concept_limit_up: dict[str, int] = {}
    for s in stocks:
        pct = s.get("pct_change", 0) or 0
        if pct < 9.5:
            continue
        for c in s.get("concepts", []):
            concept_limit_up[c] = concept_limit_up.get(c, 0) + 1

    hot_concepts_raw = sorted(
        [{"name": k, "count": len(v), "stocks": v, "limit_up": concept_limit_up.get(k, 0)}
         for k, v in concept_counter.items()],
        key=lambda x: -(x["limit_up"] * 100 + x["count"]),  # 涨停数优先, count 平局决胜
    )
    hot_concepts_raw = [c for c in hot_concepts_raw if c["limit_up"] >= 1][:5]
    hot_industries_raw = sorted(
        [{"name": k, "count": len(v), "stocks": v} for k, v in industry_counter.items()],
        key=lambda x: -x["count"],
    )[:5]

    hot_con_bk_map = {c["name"]: bk_code_map[c["name"]]
                      for c in hot_concepts_raw if c["name"] in bk_code_map}
    concept_quotes = await get_concept_板块_quotes(hot_con_bk_map)

    hot_industries = [{**c, "pct_today": 0, "pct_5day": 0} for c in hot_industries_raw]
    hot_concepts = []
    for c in hot_concepts_raw:
        cq = concept_quotes.get(c["name"], {})
        hot_concepts.append({**c,
                             "pct_today": cq.get("pct_today", 0),
                             "pct_5day": cq.get("pct_5day", 0)})

    return {"stocks": stocks, "hot_industries": hot_industries, "hot_concepts": hot_concepts}
