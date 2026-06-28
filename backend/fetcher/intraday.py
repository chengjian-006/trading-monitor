"""分时数据 (单只详细 + 批量 sparkline) - v1.7.x.

双源: 同花顺主 (d.10jqka.com.cn time), 东方财富备 (trends2/get, 2 次重试).
(主备倒置: 东财 prod 出口 IP 被封, 作主源时每次空耗 2 次重试才切备, 拖慢分时图。)
"""
import asyncio
import json
import logging
import time as _time

from backend.fetcher.codes import _code_to_em, _code_to_ths
from backend.fetcher.http_client import EM_HEADERS, THS_HEADERS, _get_client

logger = logging.getLogger(__name__)

# per-code 缓存: { code: {"pre_close": ..., "trends": [...], "_fetched_at": ts} }
# v1.7.x: 空响应不入缓存(防污染), TTL 按 code 独立计算
_batch_intraday_cache: dict = {}
_BATCH_INTRADAY_TTL = 30


def _parse_jsonp(text: str) -> dict:
    start = text.index("(") + 1
    end = text.rindex(")")
    return json.loads(text[start:end])


async def _intraday_ths(code: str) -> tuple[list[dict], float]:
    """返回 (分时点列表, 昨收)。昨收取同花顺返回的 'pre' 字段(真实昨收), 拿不到则 0。"""
    ths_code = _code_to_ths(code)
    url = f"http://d.10jqka.com.cn/v4/time/{ths_code}/last.js"
    client = _get_client()
    try:
        resp = await client.get(url, headers=THS_HEADERS)
        data = _parse_jsonp(resp.text)
        inner = data.get(ths_code, {})
        try:
            pre = float(inner.get("pre") or 0)
        except (ValueError, TypeError):
            pre = 0.0
        raw = inner.get("data", "")
        if not raw:
            return [], pre
        result = []
        from datetime import date
        today = date.today().strftime("%Y-%m-%d")
        for item in raw.split(";"):
            parts = item.split(",")
            if len(parts) < 4:
                continue
            # 逐点安全解析: 某字段为空('')时只跳过该点, 不让整条分时报废
            try:
                time_str = parts[0]
                price = float(parts[1])
                avg_price = float(parts[3]) if parts[3] else price
                volume = float(parts[4]) if len(parts) > 4 and parts[4] else 0
            except (ValueError, IndexError):
                continue
            result.append({
                "time": f"{today} {time_str[:2]}:{time_str[2:]}",
                "price": price,
                "avg_price": avg_price,
                "volume": volume,
            })
        return result, pre
    except Exception as e:
        logger.error(f"THS intraday fetch failed for {code}: {e}")
        return [], 0.0


async def get_intraday_data(code: str) -> tuple[list[dict], float]:
    """获取个股当日分时数据 (1 分钟级别) + 昨收. 同花顺主 + 东财兜底.

    v1.7.x 主备倒置: 东财 prod 出口 IP 被封, 作主源时每次都空耗 2 次重试
    (各 sleep 1s)才切备, 分时图打开要干等好几秒。同花顺返回字段一致
    (time/price/avg_price/volume + 真实昨收 pre), 直接当主源。
    返回 (分时点列表, 昨收)。昨收与分时点同源, 供分时图按"末价 vs 昨收"着色。
    """
    # 主源: 同花顺
    ths_points, ths_pre = await _intraday_ths(code)
    if ths_points:
        return ths_points, ths_pre

    # 兜底: 东方财富 (2 次重试)
    secid = _code_to_em(code)
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&iscr=0&ndays=1"
    )
    client = _get_client()
    for attempt in range(1, 3):
        try:
            resp = await client.get(url, headers=EM_HEADERS)
            data = resp.json()
            node = data.get("data") or {}
            trends = node.get("trends", [])
            if not trends:
                if attempt < 2:
                    await asyncio.sleep(1)
                    continue
                break
            try:
                pre_close = float(node.get("preClose") or 0)
            except (ValueError, TypeError):
                pre_close = 0.0
            result = []
            for t in trends:
                parts = t.split(",")
                if len(parts) >= 6:
                    result.append({
                        "time": parts[0],
                        "price": float(parts[2]),
                        "avg_price": float(parts[7]) if len(parts) > 7 else float(parts[2]),
                        "volume": float(parts[5]),
                    })
            return result, pre_close
        except Exception as e:
            logger.warning(f"[intraday] 东方财富兜底第{attempt}次失败({code}): {e}")
            if attempt < 2:
                await asyncio.sleep(1)
    logger.warning(f"[intraday] 同花顺与东财均失败({code})")
    return [], ths_pre


def _calc_5min_speed(trends: list[dict]) -> float | None:
    """5min 涨速(%) = 最新价相对 5 分钟前的涨幅. 分时为 1 分钟间隔, 取倒数第 6 个点作 5 分钟前."""
    if not trends or len(trends) < 6:
        return None
    last = trends[-1].get("price")
    ref = trends[-6].get("price")
    if not last or not ref or ref <= 0:
        return None
    return round((last - ref) / ref * 100, 2)


def get_cached_sparkline_speed(codes: list[str]) -> dict[str, float]:
    """从已缓存的分时(prefetch 每 25s 焐热)算 5min 涨速 — 纯读缓存, 不触发网络.

    给行情刷新(每 3s)的涨速兜底用: 东财 ulist 拿不到 speed 时, 用分时(同花顺→东财)
    自算涨速, 不在高频循环里发网络请求. 缓存超 90s 视为过期不用.
    """
    now = _time.time()
    out: dict[str, float] = {}
    for c in codes:
        entry = _batch_intraday_cache.get(c)
        if not entry or not entry.get("trends"):
            continue
        if now - entry.get("_fetched_at", 0) > _BATCH_INTRADAY_TTL * 3:
            continue
        s = _calc_5min_speed(entry["trends"])
        if s is not None:
            out[c] = s
    return out


async def get_batch_intraday_sparkline(codes: list[str]) -> dict:
    """批量当日分时走势 (mini sparkline 用). 缓存按 code 独立 TTL 30s; 空响应不入缓存防污染."""
    now = _time.time()
    result: dict = {}
    miss_codes: list[str] = []
    for c in codes:
        entry = _batch_intraday_cache.get(c)
        if entry and entry.get("trends") and (now - entry.get("_fetched_at", 0)) < _BATCH_INTRADAY_TTL:
            result[c] = {"pre_close": entry["pre_close"], "trends": entry["trends"]}
        else:
            miss_codes.append(c)
    if not miss_codes:
        return result

    sem = asyncio.Semaphore(5)
    client = _get_client()

    async def _fetch_one(code: str):
        """单只票分时拉取: 同花顺主 → 东财兜底."""
        async with sem:
            # 主源: 同花顺 (拿真实昨收; 拿不到宁可置0, 前端隐藏昨收线+颜色随权威涨幅, 不拿开盘当昨收)
            try:
                ths_points, ths_pre = await _intraday_ths(code)
                if ths_points:
                    trends = [
                        {"time": p["time"], "price": p["price"], "volume": p.get("volume", 0)}
                        for p in ths_points
                    ]
                    return code, {"pre_close": ths_pre, "trends": trends}
            except Exception as e:
                logger.warning(f"[batch_intraday] THS 主源失败({code}): {e}")

            # 兜底: 东方财富 (2 次重试)
            secid = _code_to_em(code)
            url = (
                f"https://push2his.eastmoney.com/api/qt/stock/trends2/get"
                f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
                f"&iscr=0&ndays=1"
            )
            for attempt in range(1, 3):
                try:
                    resp = await client.get(url, headers=EM_HEADERS)
                    data = resp.json()
                    node = data.get("data") or {}
                    pre_close = node.get("preClose", 0)
                    trends_raw = node.get("trends", [])
                    trends = []
                    for t in trends_raw:
                        parts = t.split(",")
                        if len(parts) >= 3:
                            pt = {"time": parts[0], "price": float(parts[2])}
                            if len(parts) >= 6:
                                try:
                                    pt["volume"] = float(parts[5])
                                except (ValueError, TypeError):
                                    pass
                            trends.append(pt)
                    if trends:
                        return code, {"pre_close": pre_close, "trends": trends}
                    if attempt < 2:
                        await asyncio.sleep(0.8)
                except Exception as e:
                    logger.warning(f"[batch_intraday] EM 兜底第{attempt}次失败({code}): {e}")
                    if attempt < 2:
                        await asyncio.sleep(0.8)
            return code, {"pre_close": 0, "trends": []}

    fetched = await asyncio.gather(*[_fetch_one(c) for c in miss_codes])
    for code, v in fetched:
        result[code] = v
        if v and v.get("trends"):
            _batch_intraday_cache[code] = {
                "pre_close": v["pre_close"],
                "trends": v["trends"],
                "_fetched_at": _time.time(),
            }
    return result
