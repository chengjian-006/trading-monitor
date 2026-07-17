"""实时行情 (sina 主源 + tencent 备源) - v1.7.x.

东财 prod IP 被封(v1.7.610 移除): push2 请求必败且慢失败, 空耗连接池
拖垮实时行情(0713 两次全池冻结的元凶之一)。
腾讯备源(v1.7.647): 0713 午后行情冻结致 14:40 尾盘止损检查整轮拿不到价被静默跳过
(600378 -6%止损滑到次日-19.2%才发)。备源走独立小连接池, 主池再被堵死时仍可用。
"""
import asyncio
import logging
import re
import time

import httpx

from backend.fetcher.codes import _code_to_sina
from backend.fetcher.http_client import HEADERS, TrackedAsyncClient, _get_client

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


# 备源独立小池: 不与主共享池同生死(0713 THS realhead 堵死共享池时, 走主池的备源同样会死)
_backup_client: httpx.AsyncClient | None = None


def _get_backup_client() -> httpx.AsyncClient:
    global _backup_client
    if _backup_client is None or _backup_client.is_closed:
        _backup_client = TrackedAsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            follow_redirects=True,
            trust_env=False,
        )
    return _backup_client


async def _get_quotes_tencent(codes: list[str]) -> dict:
    """腾讯行情备源(qt.gtimg.cn), 字段映射对齐 sina 口径。前缀同 sina(sh/sz)。"""
    qt_codes = [_code_to_sina(c) for c in codes]
    url = f"https://qt.gtimg.cn/q={','.join(qt_codes)}"
    client = _get_backup_client()
    try:
        resp = await client.get(url, headers={**HEADERS, "Referer": "https://gu.qq.com"})
        text = resp.content.decode("gbk", errors="replace")
    except Exception as e:
        logger.error(f"Tencent realtime fetch failed: {e}")
        return {}

    result = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        fields = line.split("=", 1)[1].strip('"').split("~")
        # 0类型 1名 2代码 3现价 4昨收 5今开 9买一价 10买一量(手) 19卖一价 20卖一量(手)
        # 33最高 34最低 36成交量(手) 37成交额(万)
        if len(fields) < 38:
            continue
        code_6 = fields[2]
        price, pre_close = _sf(fields[3]), _sf(fields[4])
        result[code_6] = {
            "code": code_6,
            "name": fields[1],
            "open": _sf(fields[5]),
            "pre_close": pre_close,
            "price": price,
            "high": _sf(fields[33]),
            "low": _sf(fields[34]),
            "volume": _sf(fields[36]) * 100,      # 手 → 股
            "amount": _sf(fields[37]) * 10000,    # 万 → 元
            "bid1_vol": _sf(fields[10]) * 100,
            "bid1_price": _sf(fields[9]),
            "ask1_vol": _sf(fields[20]) * 100,
            "ask1_price": _sf(fields[19]),
            "pct_change": round((price - pre_close) / pre_close * 100, 2) if pre_close > 0 and price > 0 else 0,
        }
    return result


async def fetch_quotes_uncached(codes: list[str]) -> dict:
    """新浪主源; 失败/空时切腾讯备源(0713 冻结漏检止损教训, v1.7.647)。"""
    try:
        result = await _get_quotes_sina(codes)
    except Exception as e:
        logger.warning(f"[quotes] 新浪行情失败: {e}")
        result = {}
    if not result:
        logger.warning("[quotes] 新浪返回空, 切腾讯备源")
        result = await _get_quotes_tencent(codes)
        if not result:
            logger.error("[quotes] 腾讯备源也返回空, 本轮实时行情不可用")
            return {}
    return {c: result[c] for c in codes if c in result}


def seed_realtime_cache(result: dict) -> None:
    """把一份完整行情结果灌回 5s 进程缓存(整轮分块抓完后调用), 维持其他消费者的缓存命中。"""
    global _realtime_cache, _realtime_cache_ts
    if result:
        _realtime_cache = dict(result)
        _realtime_cache_ts = time.time()


async def get_realtime_quotes(codes: list[str]) -> dict:
    """主源 sina, 失败/空切腾讯备源(v1.7.647). 命中 5s 进程缓存."""
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
