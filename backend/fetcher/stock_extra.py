"""股票弹性数据 (speed/turnover/volume_ratio/free_cap/industry) - v1.7.x.

同花顺 realhead 唯一源。东财 ulist.np 已移除(prod IP 被封, 请求必败且空耗连接池)。
industry 字段原来由东财补, 移除后由 quote_refresher._build_updates 从 RT 缓存取(sina 不带,
暂缺; 持仓库里已有历史值, 不影响现有功能)。
"""
import asyncio
import json
import logging
import re
import time

import httpx

from backend.fetcher.http_client import THS_HEADERS, TrackedAsyncClient

logger = logging.getLogger(__name__)

EXTRA_CACHE_TTL = 10
_extra_cache: dict = {}
_extra_cache_ts: float = 0

# ── THS realhead 独立 HTTP 池 (与 3s 行情刷新的主池物理隔离, 同 intraday.py v1.7.608 思路) ──
_ths_extra_client: httpx.AsyncClient | None = None


def _get_ths_extra_client() -> httpx.AsyncClient:
    global _ths_extra_client
    if _ths_extra_client is None or _ths_extra_client.is_closed:
        _ths_extra_client = TrackedAsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
            follow_redirects=True,
            trust_env=False,
        )
    return _ths_extra_client


# ── THS realhead 熔断: 连续全空 N 轮 → 停用 M 秒 ──
_THS_RH_FAIL_MAX = 3
_THS_RH_COOLDOWN = 300.0
_ths_rh_fail_streak = 0
_ths_rh_open_until: float = 0.0


def _ths_rh_blocked() -> bool:
    return time.monotonic() < _ths_rh_open_until


def _ths_rh_record(ok: bool) -> None:
    global _ths_rh_fail_streak, _ths_rh_open_until
    if ok:
        if _ths_rh_fail_streak >= _THS_RH_FAIL_MAX:
            logger.info("[stock_extra] THS realhead 恢复, 熔断解除")
        _ths_rh_fail_streak = 0
        _ths_rh_open_until = 0.0
        return
    _ths_rh_fail_streak += 1
    if _ths_rh_fail_streak >= _THS_RH_FAIL_MAX and not _ths_rh_blocked():
        _ths_rh_open_until = time.monotonic() + _THS_RH_COOLDOWN
        logger.warning(
            f"[stock_extra] THS realhead 连续 {_ths_rh_fail_streak} 轮全空, "
            f"熔断 {int(_THS_RH_COOLDOWN)}s")


async def _fetch_one_ths_realhead(code: str) -> dict | None:
    """同花顺 realhead 单只票弹性数据. THS 不支持 batch, 必须 per-code."""
    code_6 = str(code).zfill(6)
    url = f"http://d.10jqka.com.cn/v6/realhead/hs_{code_6}/last.js"
    client = _get_ths_extra_client()
    try:
        resp = await client.get(url, headers=THS_HEADERS)
        text = resp.text
        m = re.search(r'\((.+)\)', text)
        if not m:
            return None
        data = json.loads(m.group(1))
        items = data.get("items", {})
        if not items:
            return None
        free_shares = float(items.get("407", 0) or 0)
        price = float(items.get("10", 0) or 0)
        free_cap = (free_shares * price) / 10000.0 if free_shares > 0 and price > 0 else None
        return {
            "speed": None,
            "turnover": float(items.get("1968584")) if items.get("1968584") else None,
            "volume_ratio": float(items.get("1771976")) if items.get("1771976") else None,
            "free_cap": free_cap,
            "industry": "",
        }
    except Exception as e:
        logger.debug(f"[stock_extra] THS realhead 失败({code}): {e}")
        return None


async def _fetch_stock_extra_ths(codes: list[str]) -> dict:
    """同花顺 realhead 批量(并发)."""
    sem = asyncio.Semaphore(10)

    async def _one(c):
        async with sem:
            r = await _fetch_one_ths_realhead(c)
            await asyncio.sleep(0.05)
            return str(c).zfill(6), r

    pairs = await asyncio.gather(*[_one(c) for c in codes])
    return {code: data for code, data in pairs if data}


async def _fetch_stock_extra(codes: list[str]) -> dict:
    """THS realhead 唯一源(东财已移除). 熔断中直接返空."""
    if _ths_rh_blocked():
        return {}
    result = await _fetch_stock_extra_ths(codes)
    _ths_rh_record(bool(result))
    return result


async def get_stock_extra(codes: list[str]) -> dict:
    """统一入口: 10s 进程缓存 + 2 次重试."""
    global _extra_cache, _extra_cache_ts
    now = time.time()
    if now - _extra_cache_ts < EXTRA_CACHE_TTL and _extra_cache:
        filtered = {c: _extra_cache[c] for c in codes if c in _extra_cache}
        if len(filtered) == len(codes):
            return filtered

    result = {}
    for attempt in range(1, 3):
        result = await _fetch_stock_extra(codes)
        if result:
            break
        if attempt < 2:
            logger.warning(f"[stock_extra] 第{attempt}次返回空, 0.5s 后重试")
            await asyncio.sleep(0.5)

    if result:
        _extra_cache.update(result)
        _extra_cache_ts = now
    return {c: result.get(c, {}) for c in codes}
