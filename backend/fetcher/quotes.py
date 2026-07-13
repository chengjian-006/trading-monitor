"""实时行情 (sina 唯一源) - v1.7.x.

东财 prod IP 被封(v1.7.610 移除): push2 请求必败且慢失败, 空耗连接池
拖垮实时行情(0713 两次全池冻结的元凶之一)。
"""
import asyncio
import logging
import re
import time

from backend.fetcher.codes import _code_to_sina
from backend.fetcher.http_client import HEADERS, _get_client

logger = logging.getLogger(__name__)

REALTIME_CACHE_TTL = 5
_realtime_cache: dict = {}
_realtime_cache_ts: float = 0


def _sf(val) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_num(val, default=0):
    if isinstance(val, (int, float)):
        return val
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


async def _get_quotes_sina(codes: list[str]) -> dict:
    sina_codes = [_code_to_sina(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    client = _get_client()

    try:
        resp = await client.get(url, headers={**HEADERS, "Accept-Encoding": "identity"})
        text = resp.content.decode("gbk", errors="replace")
    except Exception as e:
        logger.error(f"Sina realtime fetch failed: {e}")
        return {}

    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or '="' not in line:
            continue
        match = re.match(r'var hq_str_(\w+)="(.*)";?', line)
        if not match:
            continue
        sina_code = match.group(1)
        fields = match.group(2).split(",")
        code_6 = sina_code[2:]
        if len(fields) < 32:
            continue
        try:
            result[code_6] = {
                "code": code_6,
                "name": fields[0],
                "open": _sf(fields[1]),
                "pre_close": _sf(fields[2]),
                "price": _sf(fields[3]),
                "high": _sf(fields[4]),
                "low": _sf(fields[5]),
                "volume": _sf(fields[8]),
                "amount": _sf(fields[9]),
                # 五档一档(供持仓异动算封单: 涨停封死=卖一量0/封单=买一量×买一价, 跌停反之)
                "bid1_vol": _sf(fields[10]),   # 买一量(股)
                "bid1_price": _sf(fields[11]),
                "ask1_vol": _sf(fields[20]),   # 卖一量(股)
                "ask1_price": _sf(fields[21]),
                # price=0(竞价未撮合/停牌)时不能按公式算, 否则得 -100% 脏值
                "pct_change": round((_sf(fields[3]) - _sf(fields[2])) / _sf(fields[2]) * 100, 2) if _sf(fields[2]) > 0 and _sf(fields[3]) > 0 else 0,
            }
        except (IndexError, ValueError):
            continue
    return result


async def fetch_quotes_uncached(codes: list[str]) -> dict:
    """新浪行情(prod 唯一可用源, 东财已封)。"""
    try:
        result = await _get_quotes_sina(codes)
    except Exception as e:
        logger.warning(f"[quotes] 新浪行情失败: {e}")
        return {}
    if not result:
        logger.warning("[quotes] 新浪返回空")
    return {c: result[c] for c in codes if c in result}


def seed_realtime_cache(result: dict) -> None:
    """把一份完整行情结果灌回 5s 进程缓存(整轮分块抓完后调用), 维持其他消费者的缓存命中。"""
    global _realtime_cache, _realtime_cache_ts
    if result:
        _realtime_cache = dict(result)
        _realtime_cache_ts = time.time()


async def get_realtime_quotes(codes: list[str]) -> dict:
    """主源 sina (1次), 失败/空切东财(2次重试). 命中 5s 进程缓存."""
    global _realtime_cache, _realtime_cache_ts
    now = time.time()
    if now - _realtime_cache_ts < REALTIME_CACHE_TTL and _realtime_cache:
        filtered = {c: _realtime_cache[c] for c in codes if c in _realtime_cache}
        if len(filtered) == len(codes):
            return filtered

    result = await fetch_quotes_uncached(codes)
    if result:
        _realtime_cache = result
        _realtime_cache_ts = now
    return result
