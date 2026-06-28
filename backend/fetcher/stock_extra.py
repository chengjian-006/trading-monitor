"""股票弹性数据 (speed/turnover/volume_ratio/free_cap/industry) - v1.7.78 主备倒置后,
同花顺 realhead 为主 (THS 实时性更好), 东财 ulist.np 为备 (补行业, batch 调用更高效).
"""
import asyncio
import json
import logging
import re
import time

from backend.fetcher.codes import _code_to_em
from backend.fetcher.http_client import EM_HEADERS, THS_HEADERS, _get_client
from backend.fetcher.quotes import _safe_num

logger = logging.getLogger(__name__)

EXTRA_CACHE_TTL = 10
_extra_cache: dict = {}
_extra_cache_ts: float = 0


async def _fetch_stock_extra_eastmoney(codes: list[str]) -> dict:
    """东财 ulist.np extra fields — 包含 industry."""
    secids = ",".join(_code_to_em(c) for c in codes)
    url = (
        f"https://push2.eastmoney.com/api/qt/ulist.np/get"
        f"?fltt=2&secids={secids}"
        f"&fields=f8,f10,f11,f12,f14,f21,f100"
    )
    client = _get_client()
    try:
        resp = await client.get(url, headers=EM_HEADERS)
        data = resp.json()
    except Exception as e:
        logger.error(f"East Money extra fetch failed: {e}")
        return {}

    result = {}
    diff_list = data.get("data", {}).get("diff", []) if data.get("data") else []
    for item in diff_list:
        code_6 = str(item.get("f12", "")).zfill(6)
        industry_raw = item.get("f100", "")
        result[code_6] = {
            "speed": _safe_num(item.get("f11")),
            "turnover": _safe_num(item.get("f8")),
            "volume_ratio": _safe_num(item.get("f10")),
            "free_cap": _safe_num(item.get("f21")),
            "industry": industry_raw if isinstance(industry_raw, str) and industry_raw != "-" else "",
        }
    return result


async def _fetch_one_ths_realhead(code: str) -> dict | None:
    """同花顺 realhead 单只票弹性数据. THS 不支持 batch, 必须 per-code."""
    code_6 = str(code).zfill(6)
    url = f"http://d.10jqka.com.cn/v6/realhead/hs_{code_6}/last.js"
    client = _get_client()
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
        # 流通市值 = 流通股本 × 现价 (单位: 万元 = 流通股股数 × 价格 / 10000)
        free_shares = float(items.get("407", 0) or 0)
        price = float(items.get("10", 0) or 0)
        free_cap = (free_shares * price) / 10000.0 if free_shares > 0 and price > 0 else None
        # 461256 是"年内涨速", 不是 5min 涨速, 暂留空
        return {
            "speed": None,
            # 1968584=换手率(‰, 需÷10转%), 1771976=量比
            "turnover": float(items.get("1968584")) / 10 if items.get("1968584") else None,
            "volume_ratio": float(items.get("1771976")) if items.get("1771976") else None,
            "free_cap": free_cap,
            "industry": "",
        }
    except Exception as e:
        logger.debug(f"[stock_extra] THS realhead 失败({code}): {e}")
        return None


async def _fetch_stock_extra_ths(codes: list[str]) -> dict:
    """同花顺 realhead 批量(并发) — 主备倒置后的主源."""
    sem = asyncio.Semaphore(10)

    async def _one(c):
        async with sem:
            r = await _fetch_one_ths_realhead(c)
            await asyncio.sleep(0.05)
            return str(c).zfill(6), r

    pairs = await asyncio.gather(*[_one(c) for c in codes])
    return {code: data for code, data in pairs if data}


def _merge_extra(primary: dict, fallback: dict) -> dict:
    """primary 缺的字段用 fallback 补 (主要补 industry)."""
    out = dict(primary)
    for k, v in fallback.items():
        if not out.get(k):
            out[k] = v
    return out


async def _fetch_stock_extra(codes: list[str]) -> dict:
    """v1.7.78: THS realhead 主, 东财备. THS 不带行业, 行业从东财补."""
    ths_result = await _fetch_stock_extra_ths(codes)
    if ths_result:
        try:
            em_result = await _fetch_stock_extra_eastmoney(codes)
            for c, d in ths_result.items():
                em = em_result.get(c, {})
                if not d.get("industry") and em.get("industry"):
                    d["industry"] = em["industry"]
                # THS realhead 不带 5min 涨速 (写死 None), 用东财 f11 补
                if d.get("speed") is None and em.get("speed") is not None:
                    d["speed"] = em["speed"]
        except Exception:
            pass
        return ths_result
    logger.warning("[stock_extra] THS 全失败, 回退东财")
    return await _fetch_stock_extra_eastmoney(codes)


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
