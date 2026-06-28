"""全市场日线回填 + 每日追加 - 工作流二 A 期.

复用 market_breadth_refresher 同源路径: 新浪全A列表(Market_Center hs_a) + 新浪
getKLineData(scale=240, ma=no). 非东财, prod 安全. 落 cfzy_sys_kline_cache(幂等 upsert).
- 一次性回填: backfill_full_market(datalen≈1300 → 近5年), 断点续跑(已≥MIN_BARS的票跳过).
- 每日追加: append_full_market_daily(datalen=8 → 刷新最近几日), 收盘后定时跑.
"""
import json
import logging

logger = logging.getLogger(__name__)

_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_KLINE_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/data/"
              "CN_MarketDataService.getKLineData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
_CONCURRENCY = 8          # < 连接池 maxsize(10), 同一信号量同时限拉取+写库, 不抢占实盘查询
BACKFILL_DATALEN = 1300   # ≈5 年交易日(实测新浪按 datalen 返回, 1300 够5年)
APPEND_DATALEN = 8        # 每日追加只需最近几根
MIN_BARS = 1000           # 已缓存≥此根数视为已回填, 回填时跳过(断点续跑)


def _parse_sina_klines(text: str) -> list[tuple]:
    """新浪 getKLineData jsonp 文本 → [(date, open, high, low, close, volume), ...].

    取首个 '(' 与末个 ')' 之间的 JSON 数组(与 fetcher/klines.py 同款). 解析失败/空 → [].
    """
    s = text.find("(")
    e = text.rfind(")")
    if s < 0 or e <= s:
        return []
    try:
        data = json.loads(text[s + 1:e])
    except (json.JSONDecodeError, ValueError):
        return []
    if not data:
        return []
    rows: list[tuple] = []
    for d in data:
        try:
            rows.append((
                str(d["day"])[:10], float(d["open"]), float(d["high"]),
                float(d["low"]), float(d["close"]), float(d["volume"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _filter_symbols(rows: list[dict]) -> list[str]:
    """Market_Center 行 → 新浪 symbol 列表, 剔北交所(bj)/ST/退市/*."""
    out: list[str] = []
    for it in rows:
        sym = it.get("symbol", "")
        name = it.get("name", "")
        if sym.startswith("bj") or not (sym.startswith("sh") or sym.startswith("sz")):
            continue
        if "ST" in name or "退" in name or name.startswith("*"):
            continue
        out.append(sym)
    return out


def _needs_backfill(cached_count: int, min_bars: int) -> bool:
    """已缓存根数不足 min_bars → 需要(继续)回填."""
    return cached_count < min_bars


import asyncio

import httpx

from backend.fetcher.codes import _normalize_code
from backend.models import repository


async def _fetch_symbols(client: httpx.AsyncClient) -> list[str]:
    """新浪 hs_a 全A列表(分页), 经 _filter_symbols 剔除. 返回新浪 symbol(如 sh600519)."""
    out: list[str] = []
    page = 1
    while page <= 90:
        params = {"page": page, "num": 80, "sort": "symbol", "asc": 1,
                  "node": "hs_a", "symbol": "", "_s_r_a": "page"}
        try:
            r = await client.get(_LIST_URL, params=params, headers=_HEADERS)
            txt = (r.text or "").strip()
        except Exception as e:
            logger.warning(f"[fullkline] 列表第{page}页失败: {e}")
            break
        if not txt or txt == "null":
            break
        try:
            rows = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            break
        if not rows:
            break
        out.extend(_filter_symbols(rows))
        page += 1
    return out


async def _fetch_klines(client: httpx.AsyncClient, sym: str, datalen: int) -> list[tuple]:
    url = f"{_KLINE_URL}?symbol={sym}&scale=240&ma=no&datalen={datalen}"
    try:
        r = await client.get(url, headers=_HEADERS)
        return _parse_sina_klines(r.text)
    except Exception:
        return []


async def _run_full_market(datalen: int, only_missing: bool) -> dict:
    """全市场逐只拉日线写缓存. only_missing=True 跳过已≥MIN_BARS的票(回填续跑)."""
    counts = await repository.get_kline_counts() if only_missing else {}
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        trust_env=False,
    )
    ok = skipped = empty = done = 0
    try:
        symbols = await _fetch_symbols(client)
        total = len(symbols)
        if total == 0:
            logger.warning("[fullkline] 全市场列表为空, 跳过")
            return {"total": 0, "ok": 0, "skipped": 0, "empty": 0}
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _one(sym: str):
            nonlocal ok, skipped, empty, done
            code = _normalize_code(sym)
            if only_missing and not _needs_backfill(counts.get(code, 0), MIN_BARS):
                skipped += 1
                done += 1
                return
            async with sem:  # 同一信号量同时限制拉取+写库, 峰值并发 < 连接池, 不饿死实盘查询
                rows = await _fetch_klines(client, sym, datalen)
                if rows:
                    try:
                        await repository.cache_klines(code, rows)
                        ok += 1
                    except Exception as e:
                        empty += 1
                        logger.debug(f"[fullkline] 写库失败 {code}: {e}")
                else:
                    empty += 1
            done += 1
            if done % 500 == 0:
                logger.info(f"[fullkline] {done}/{total} ok={ok} skip={skipped} empty={empty}")

        await asyncio.gather(*[_one(s) for s in symbols])
    finally:
        await client.aclose()
    logger.info(f"[fullkline] DONE total={total} ok={ok} skip={skipped} empty={empty}")
    return {"total": total, "ok": ok, "skipped": skipped, "empty": empty}


async def backfill_full_market() -> dict:
    """一次性回填全市场近5年日线(断点续跑). 手动脚本触发, 耗时较长."""
    return await _run_full_market(BACKFILL_DATALEN, only_missing=True)


async def append_full_market_daily() -> dict:
    """每日收盘后给全市场补最近几日日线(全量 upsert 刷新最新交易日)."""
    return await _run_full_market(APPEND_DATALEN, only_missing=False)
