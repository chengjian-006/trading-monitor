"""实时行情 (sina 主源 + eastmoney 备源) - v1.7.x.

主备倒置历史 (v1.7.74): 东财对 prod IP 频繁风控/断连, 新浪稳定性更高.
失败链: sina(1次) → eastmoney(2次重试).
"""
import asyncio
import logging
import re
import time

from backend.fetcher.codes import _code_to_em, _code_to_sina
from backend.fetcher.http_client import EM_HEADERS, HEADERS, _get_client

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


async def _get_quotes_eastmoney(codes: list[str]) -> dict:
    result = {}
    batch_size = 40
    client = _get_client()
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        secids = ",".join(_code_to_em(c) for c in batch)
        url = (
            f"https://push2.eastmoney.com/api/qt/ulist.np/get"
            f"?fltt=2&secids={secids}"
            f"&fields=f2,f3,f5,f6,f8,f10,f11,f12,f14,f21,f100"
        )
        try:
            resp = await client.get(url, headers=EM_HEADERS)
            data = resp.json()
            diff = data.get("data", {}).get("diff", []) if data.get("data") else []
            for item in diff:
                code_6 = str(item.get("f12", "")).zfill(6)
                price = item.get("f2", 0) or 0
                pct = item.get("f3", 0) or 0
                amount = item.get("f6", 0) or 0
                name = item.get("f14", "") or ""
                speed = _safe_num(item.get("f11"))
                turnover = _safe_num(item.get("f8"))
                volume_ratio = _safe_num(item.get("f10"))
                free_cap = _safe_num(item.get("f21"))
                industry_raw = item.get("f100", "")
                industry = industry_raw if isinstance(industry_raw, str) and industry_raw != "-" else ""
                result[code_6] = {
                    "code": code_6,
                    "name": name,
                    "price": price,
                    "pct_change": pct,
                    "amount": amount,
                    "speed": speed,
                    "turnover": turnover,
                    "volume_ratio": volume_ratio,
                    "free_cap": free_cap,
                    "industry": industry,
                    "open": 0, "pre_close": 0, "high": 0, "low": 0, "volume": 0,
                }
        except Exception as e:
            logger.error(f"EastMoney realtime fetch failed (batch {i}): {e}")
    return result


async def fetch_quotes_uncached(codes: list[str]) -> dict:
    """绕过 5s 进程缓存直接抓一批(sina 1次 → 东财2次重试), 不读不写缓存。

    v1.7.562: 供 quote_refresher 分块抓取+逐块落库用(源慢时拿到一块落一块,
    不因单轮超时整轮作废); 整轮抓完由调用方 seed_realtime_cache 灌回缓存。
    """
    result = {}
    try:
        result = await _get_quotes_sina(codes)
    except Exception as e:
        logger.warning(f"[quotes] 新浪失败({e}), 切换东方财富")

    if not result:
        logger.warning("[quotes] 新浪返回空, 切换东方财富")
        for attempt in range(1, 3):
            try:
                result = await _get_quotes_eastmoney(codes)
                if result:
                    logger.info("[quotes] 东财备源成功")
                    break
            except Exception as e:
                logger.warning(f"[quotes] 东财第{attempt}次失败: {e}")
            if attempt < 2:
                await asyncio.sleep(0.4)  # 东财仅备源且热路径(3s周期), 退避缩短减少放大
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
