"""板块/概念相关 - v1.7.x.

6 个核心函数:
  get_stock_concepts          — 个股 → 概念名列表 (跳过无意义"基金重仓"等公共标签)
  get_concept_板块_quotes     — 概念名 + BK code → 板块行情(今日/5日涨幅)
  get_industry_bk_map         — 行业名 → BK code 的全表映射 (一次拉 500 条)
  get_sector_ranking          — 全市场行业板块当日涨幅 TOP N, 含 DB stale fallback
  get_sector_overview         — 单个行业的综合信息(板块涨幅+龙头+前N) + DB stale fallback
  get_sector_top_stocks       — 板块内涨幅前 N 股票
"""
import asyncio
import logging
import time

from backend.fetcher.http_client import EM_HEADERS, HEADERS, _get_client

logger = logging.getLogger(__name__)

_sector_overview_cache: dict[str, tuple[float, dict]] = {}
SECTOR_OVERVIEW_TTL = 30

_sector_ranking_cache: tuple[float, list[dict]] | None = None
SECTOR_RANKING_TTL = 60


async def get_stock_concepts(codes: list[str], client=None) -> tuple[dict[str, list[str]], dict[str, str]]:
    skip = {"基金重仓", "融资融券", "昨日打二板以上表现", "昨日连板_含一字",
            "昨日连板", "昨日炸板", "昨日涨停_含一字", "昨日涨停",
            "MSCI概念", "百元股", "百日新高", "QFII重仓",
            "深股通", "沪股通", "创业板综"}
    result: dict[str, list[str]] = {}
    bk_code_map: dict[str, str] = {}
    sem = asyncio.Semaphore(5)

    async def fetch_one(code: str):
        prefix = "1" if code.startswith(("6", "9")) else "0"
        url = (f"https://push2.eastmoney.com/api/qt/slist/get"
               f"?secid={prefix}.{code}&pn=1&ps=10&spt=3"
               f"&fields=f12,f14&fid=f3&po=1&np=1&invt=2"
               f"&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2")
        _client = client or _get_client()
        concepts: list[str] = []
        local_bk: dict[str, str] = {}
        async with sem:
            for attempt in range(1, 4):
                try:
                    resp = await _client.get(url, headers=EM_HEADERS)
                    data = resp.json()
                    diff = data.get("data", {}).get("diff", []) if data.get("data") else []
                    for d in diff:
                        name = d.get("f14", "")
                        bk = d.get("f12", "")
                        if name and name not in skip and bk:
                            local_bk[name] = bk
                    concepts = [d.get("f14", "") for d in diff
                                if d.get("f14", "") and d.get("f14", "") not in skip]
                    if concepts:
                        break
                except Exception:
                    if attempt < 3:
                        await asyncio.sleep(1)
        return code, concepts[:8], local_bk   # v1.7.x: 5→8, 给下游噪音过滤留余量(指数成分/风格标签过滤后仍保住真题材)

    tasks = await asyncio.gather(*[fetch_one(c) for c in codes])
    for code, concepts, local_bk in tasks:
        result[code] = concepts
        bk_code_map.update(local_bk)
    return result, bk_code_map


async def get_concept_板块_quotes(bk_code_map: dict[str, str], client=None,
                                  with_5day: bool = True) -> dict[str, dict]:
    if not bk_code_map:
        return {}
    bk_codes = list(set(bk_code_map.values()))
    code_to_name = {v: k for k, v in bk_code_map.items()}

    secids = ",".join(f"90.{bk}" for bk in bk_codes)
    url = (f"https://push2.eastmoney.com/api/qt/ulist.np/get"
           f"?fltt=2&secids={secids}&fields=f3,f12,f14")
    client = client or _get_client()
    result: dict[str, dict] = {}
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
        for item in diff:
            bk = item.get("f12", "")
            name = item.get("f14", "") or code_to_name.get(bk, "")
            pct = item.get("f3", 0) or 0
            result[name] = {"pct_today": pct, "pct_5day": 0, "bk_code": bk}
    except Exception as e:
        logger.error(f"Concept板块 quote fetch failed: {e}")

    if not with_5day:
        return result   # 只需当日涨幅(挑最热题材)时跳过逐板块 5 日 K 线, 省一大批外部请求

    sem = asyncio.Semaphore(5)

    async def fetch_5day(bk: str):
        name = code_to_name.get(bk, "")
        if not name or name not in result:
            return
        kline_url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
                     f"?secid=90.{bk}&fields1=f1,f2,f3&fields2=f51,f52,f53"
                     f"&klt=101&fqt=1&end=20500101&lmt=6")
        async with sem:
            try:
                resp = await client.get(kline_url, headers=EM_HEADERS)
                kdata = resp.json()
                klines = kdata.get("data", {}).get("klines", []) if kdata.get("data") else []
                if len(klines) >= 2:
                    first_close = float(klines[0].split(",")[2])
                    last_close = float(klines[-1].split(",")[2])
                    if first_close > 0:
                        result[name]["pct_5day"] = round((last_close - first_close) / first_close * 100, 2)
            except Exception:
                pass

    await asyncio.gather(*[fetch_5day(bk) for bk in bk_codes])
    return result


async def get_industry_bk_map() -> dict[str, str]:
    client = _get_client()
    result = {}
    for page in range(1, 6):
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
               f"&fs=m:90+t:2&fields=f12,f14")
        try:
            resp = await client.get(url, headers=EM_HEADERS)
            if not resp.content:
                break
            data = resp.json()
            diff = data.get("data", {}).get("diff", []) if data.get("data") else []
            if not diff:
                break
            for item in diff:
                name = item.get("f14", "")
                bk = item.get("f12", "")
                if name and bk:
                    result[name] = bk
        except Exception as e:
            logger.error(f"Industry BK map page {page} failed: {e}")
            break
    return result


async def _sector_ranking_tencent() -> list[dict]:
    """行业板块榜备源 — 腾讯 (东财 prod IP 被封时兜底).

    东财主源挂时该源接管, 仅产出 {rank, industry, pct_today}; 拿不到东财 bk_code,
    故 bk_code 填合成值 tx_<name> 仅保唯一(热力图只展示名+涨幅、格子不可点, 不影响)。
    板块下钻(get_sector_overview) 另走 get_industry_bk_map, 与本备源无关。
    任何异常/空结果都返回 [] 让上层继续走 DB 兜底, 不会使现状变差。
    """
    client = _get_client()
    # v1.7.387: 腾讯接口改版 — 旧 sort=3 现直接 400 ("invalid CommonReq.SortType"),
    # 改用 sort_type=price(实测唯一可用值), 涨跌幅排序拉回后在客户端按 zdf 做。
    # 行业(hy)共31个一级行业, count=200 一把拉全。
    url = ("https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
           "?board_type=hy&sort_type=price&direct=down&offset=0&count=200")
    out: list[dict] = []
    try:
        resp = await client.get(url, headers=HEADERS)
        data = resp.json()
        d = data.get("data") if isinstance(data, dict) else None
        rows = (d.get("rank_list") or []) if isinstance(d, dict) else []
        parsed = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or ""
            pct_raw = item.get("zdf")
            if not name or pct_raw is None:
                continue
            try:
                pct = float(str(pct_raw).replace("%", ""))
            except (TypeError, ValueError):
                continue
            parsed.append((name, pct))
        parsed.sort(key=lambda x: x[1], reverse=True)
        out = [{"rank": i, "industry": name, "bk_code": f"tx_{name}", "pct_today": pct}
               for i, (name, pct) in enumerate(parsed, 1)]
    except Exception as e:
        logger.warning(f"[sector_ranking] 腾讯备源取数失败: {e}")
    return out


async def get_sector_ranking(top_n: int = 30) -> list[dict]:
    """全市场行业板块当日涨幅 TOP N. 东财主源 → 腾讯备源 → DB stale fallback (最多 2h)."""
    global _sector_ranking_cache
    now = time.time()
    if _sector_ranking_cache and now - _sector_ranking_cache[0] < SECTOR_RANKING_TTL:
        return _sector_ranking_cache[1][:top_n]

    client = _get_client()
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
           f"&fs=m:90+t:2&fields=f3,f12,f14")
    result: list[dict] = []
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
        for i, item in enumerate(diff, 1):
            name = item.get("f14", "")
            bk = item.get("f12", "")
            pct = item.get("f3")
            if not name or not bk or pct is None:
                continue
            result.append({"rank": i, "industry": name, "bk_code": bk, "pct_today": float(pct)})
    except Exception as e:
        logger.warning(f"[sector_ranking] 东财主源取数失败: {e}")

    # 东财空/失败 → 腾讯备源 (v1.7.x: 东财 prod IP 被封, 板块榜原为单源)
    if not result:
        result = await _sector_ranking_tencent()
        if result:
            logger.info(f"[sector_ranking] 东财不可用, 已由腾讯备源接管 ({len(result)}条)")

    if result:
        _sector_ranking_cache = (now, result)
        try:
            from backend.models import repository
            await repository.api_cache_set("sector_ranking", result)
        except Exception as e:
            logger.debug(f"[sector_ranking] DB 缓存写入失败 (忽略): {e}")
        return result[:top_n]

    try:
        from backend.models import repository
        cached, age = await repository.api_cache_get("sector_ranking", max_stale_seconds=7200)
        if cached:
            logger.warning(f"[sector_ranking] 外部接口空/失败, 回退 DB (stale {age}s, {len(cached)}条)")
            _sector_ranking_cache = (now - SECTOR_RANKING_TTL + 30, cached)
            return cached[:top_n]
    except Exception as e:
        logger.warning(f"[sector_ranking] DB 回退失败: {e}")
    return []


async def get_sector_top_stocks(bk_code: str, top_n: int = 5) -> list[dict]:
    """板块内涨幅前 N 股票."""
    client = _get_client()
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?pn=1&pz={top_n}&po=1&np=1&fltt=2&invt=2"
           f"&fid=f3&fs=b:{bk_code}&fields=f3,f12,f14")
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
        result = []
        for item in diff:
            code = item.get("f12", "")
            pct = item.get("f3")
            name = item.get("f14", "")
            if code and pct is not None:
                result.append({"code": code, "name": name, "pct_change": float(pct)})
        return result
    except Exception as e:
        logger.error(f"Sector top stocks fetch failed for {bk_code}: {e}")
        return []


async def get_board_all_pct(bk_code: str, limit: int = 500, client=None) -> list[float]:
    """板块(行业/概念 BK)内全部成分股的当日涨幅, 按降序返回(接口 fid=f3 po=1 本就降序)。

    给"持仓在板块内强弱名次"用: 名单按涨幅排好后, 拿持仓实时涨幅二分定位即得名次/分位,
    无需逐只比对。整板拉一次(pz=500 覆盖绝大多数概念板块规模)。失败返回 []。
    """
    if not bk_code:
        return []
    client = client or _get_client()
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2"
           f"&fid=f3&fs=b:{bk_code}&fields=f3")
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
        diff = data.get("data", {}).get("diff", []) if data.get("data") else []
        out: list[float] = []
        for item in diff:
            pct = item.get("f3")
            if pct is not None:
                try:
                    out.append(float(pct))
                except (TypeError, ValueError):
                    continue
        return out
    except Exception as e:
        logger.error(f"Board all-pct fetch failed for {bk_code}: {e}")
        return []


async def get_sector_overview(industry_name: str, top_n: int = 5) -> dict | None:
    """板块综合: 板块涨幅 + 龙头 + 前N. 接 DB stale fallback (最多 30min)."""
    if not industry_name:
        return None
    now = time.time()
    cache_key = f"{industry_name}|{top_n}"
    cached = _sector_overview_cache.get(cache_key)
    if cached and now - cached[0] < SECTOR_OVERVIEW_TTL:
        return cached[1]

    bk_map = await get_industry_bk_map()
    bk_code = bk_map.get(industry_name)
    if not bk_code:
        return None

    quotes_task = get_concept_板块_quotes({industry_name: bk_code})
    top_task = get_sector_top_stocks(bk_code, top_n=top_n)
    quotes, top_stocks = await asyncio.gather(quotes_task, top_task)

    sector_quote = quotes.get(industry_name, {})
    pct_today = float(sector_quote.get("pct_today", 0) or 0)

    leader_name = ""
    leader_pct = 0.0
    if top_stocks:
        leader_name = top_stocks[0].get("name", "")
        leader_pct = float(top_stocks[0].get("pct_change", 0) or 0)

    result = {
        "industry": industry_name, "bk_code": bk_code,
        "pct_today": pct_today,
        "leader_name": leader_name, "leader_pct": leader_pct,
        "top_stocks": top_stocks,
    }
    is_success = bool(top_stocks) and bool(leader_name)
    if is_success:
        _sector_overview_cache[cache_key] = (now, result)
        try:
            from backend.models import repository
            await repository.api_cache_set(f"sector_overview:{industry_name}:{top_n}", result)
        except Exception as e:
            logger.debug(f"[sector_overview] DB 缓存写入失败 (忽略): {e}")
        return result

    try:
        from backend.models import repository
        cached_v, age = await repository.api_cache_get(
            f"sector_overview:{industry_name}:{top_n}", max_stale_seconds=1800
        )
        if cached_v:
            logger.warning(f"[sector_overview] {industry_name} 外部空/失败, 回退 DB (stale {age}s)")
            _sector_overview_cache[cache_key] = (now - SECTOR_OVERVIEW_TTL + 10, cached_v)
            return cached_v
    except Exception as e:
        logger.warning(f"[sector_overview] {industry_name} DB 回退失败: {e}")
    return result
