"""股票代码/名称搜索 - sina suggest 主, akshare 全量列表兜底."""
import logging
import re
import time

from backend.fetcher.http_client import HEADERS, _get_client

logger = logging.getLogger(__name__)

_stock_list_cache: list = []
_stock_list_ts: float = 0


async def search_stock(keyword: str) -> list[dict]:
    """新浪 suggest 接口实时搜索. 失败回退 akshare 全量列表."""
    if not keyword:
        return []

    url = f"https://suggest3.sinajs.cn/suggest/type=11,12&key={keyword}&name=suggestdata"
    client = _get_client()
    try:
        resp = await client.get(url, headers={**HEADERS, "Accept-Encoding": "identity"})
        text = resp.content.decode("gbk", errors="replace")
    except Exception:
        return await _search_fallback(keyword)

    match = re.search(r'"(.+)"', text)
    if not match:
        return await _search_fallback(keyword)

    results = []
    for item in match.group(1).split(";"):
        parts = item.split(",")
        if len(parts) >= 4:
            code_raw = parts[2] if len(parts) > 2 else ""
            name = parts[4] if len(parts) > 4 else parts[0]
            code = re.sub(r'\D', '', code_raw)
            if code and len(code) == 6:
                results.append({"code": code, "name": name})

    return results[:10]


async def _search_fallback(keyword: str) -> list[dict]:
    """akshare 全量 A 股列表兜底 (24h 缓存)."""
    global _stock_list_cache, _stock_list_ts

    if not _stock_list_cache or time.time() - _stock_list_ts > 86400:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            _stock_list_cache = [
                {"code": str(r["代码"]).zfill(6), "name": str(r["名称"])}
                for _, r in df.iterrows()
            ]
            _stock_list_ts = time.time()
        except Exception:
            return []

    results = []
    for s in _stock_list_cache:
        if keyword in s["code"] or keyword in s["name"]:
            results.append(s)
            if len(results) >= 10:
                break
    return results
