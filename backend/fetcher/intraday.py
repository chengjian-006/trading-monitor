"""分时数据 (单只详细 + 批量 sparkline) - v1.7.x.

同花顺唯一源 (d.10jqka.com.cn time)。东财已移除(v1.7.610): prod IP 被封,
兜底请求必败且慢失败, 0713 拖垮行情的元凶。
"""
import asyncio
import json
import logging
import time as _time

import httpx

from backend.fetcher.codes import _code_to_ths
from backend.fetcher.http_client import THS_HEADERS, TrackedAsyncClient

logger = logging.getLogger(__name__)

# per-code 缓存: { code: {"pre_close": ..., "trends": [...], "_fetched_at": ts} }
# v1.7.x: 空响应不入缓存(防污染), TTL 按 code 独立计算
_batch_intraday_cache: dict = {}
_BATCH_INTRADAY_TTL = 30
# 非交易时段分时不再变化, 30s TTL 会让整晚每次打开页面都全量重拉上游 → 放长到1小时
_BATCH_INTRADAY_TTL_CLOSED = 3600
# 单次请求的上游拉取预算(秒): 到点先把拿到的返回, 没拉完的留后台继续焐缓存,
# 前端 30s 轮询按 code 合并会自动补全 — 防止冷缓存时一次请求挂几分钟(0703实测140s)
_BATCH_FETCH_BUDGET = 8.0

# 飞行中拉取去重: 同一 code 的上游拉取全局只跑一份(3个分块请求并行+30s轮询会重复触发)
_inflight: dict = {}
# 全局上游并发闸(模块级, 懒建防跨事件循环): 原来每请求各开 Semaphore(5), 3个分块并行=15路
_fetch_sem: asyncio.Semaphore | None = None
_fetch_sem_loop = None


def _get_fetch_sem() -> asyncio.Semaphore:
    global _fetch_sem, _fetch_sem_loop
    loop = asyncio.get_running_loop()
    if _fetch_sem is None or _fetch_sem_loop is not loop:
        _fetch_sem = asyncio.Semaphore(6)
        _fetch_sem_loop = loop
    return _fetch_sem


# ── 护栏1: 独立 HTTP 客户端 (与 3s 实时行情的主池物理隔离, 分时再堵也堵不到行情) ──
# 池子按 _fetch_sem(6) 配, 超时比主池(15s)短: 分时是非关键路径, 宁可拿不到也不能拖住别人。
_intraday_client: httpx.AsyncClient | None = None


def _get_intraday_client() -> httpx.AsyncClient:
    global _intraday_client
    if _intraday_client is None or _intraday_client.is_closed:
        _intraday_client = TrackedAsyncClient(   # 仍走 TrackedAsyncClient, 保留 api_metrics 打点
            timeout=httpx.Timeout(6.0, connect=3.0),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=3),
            follow_redirects=True,
            trust_env=False,
        )
    return _intraday_client


def _batch_ttl() -> float:
    """盘中 30s; 非交易时段分时冻结, 1小时。"""
    try:
        from backend.core.trading_calendar import is_trading_time
        return _BATCH_INTRADAY_TTL if is_trading_time() else _BATCH_INTRADAY_TTL_CLOSED
    except Exception:
        return _BATCH_INTRADAY_TTL


def _parse_jsonp(text: str) -> dict:
    start = text.index("(") + 1
    end = text.rindex(")")
    return json.loads(text[start:end])


async def _intraday_ths(code: str) -> tuple[list[dict], float]:
    """返回 (分时点列表, 昨收)。昨收取同花顺返回的 'pre' 字段(真实昨收), 拿不到则 0。"""
    ths_code = _code_to_ths(code)
    url = f"http://d.10jqka.com.cn/v4/time/{ths_code}/last.js"
    client = _get_intraday_client()
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
    """获取个股当日分时数据 (1 分钟级别) + 昨收. 同花顺唯一源.
    返回 (分时点列表, 昨收)。昨收与分时点同源, 供分时图按"末价 vs 昨收"着色。
    """
    ths_points, ths_pre = await _intraday_ths(code)
    return ths_points, ths_pre


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


async def _fetch_one_sparkline(code: str) -> dict:
    """单只票分时拉取(同花顺唯一源). 成功即写缓存."""
    async with _get_fetch_sem():
        v = {"pre_close": 0, "trends": []}
        try:
            ths_points, ths_pre = await _intraday_ths(code)
            if ths_points:
                v = {"pre_close": ths_pre,
                     "trends": [{"time": p["time"], "price": p["price"],
                                 "volume": p.get("volume", 0)} for p in ths_points]}
        except Exception as e:
            logger.warning(f"[batch_intraday] THS 失败({code}): {e}")

        if v.get("trends"):
            _batch_intraday_cache[code] = {
                "pre_close": v["pre_close"], "trends": v["trends"],
                "_fetched_at": _time.time(),
            }
        return v


async def get_batch_intraday_sparkline(codes: list[str]) -> dict:
    """批量当日分时走势 (mini sparkline 用). 缓存按 code 独立 TTL(盘中30s/盘后1h);
    空响应不入缓存防污染。单次请求上游拉取有预算(8s): 到点先返回已拿到的,
    未完成的留后台继续写缓存, 前端 30s 轮询按 code 合并自动补全。"""
    now = _time.time()
    ttl = _batch_ttl()
    result: dict = {}
    miss_codes: list[str] = []
    for c in codes:
        entry = _batch_intraday_cache.get(c)
        if entry and entry.get("trends") and (now - entry.get("_fetched_at", 0)) < ttl:
            result[c] = {"pre_close": entry["pre_close"], "trends": entry["trends"]}
        else:
            miss_codes.append(c)
    if not miss_codes:
        return result

    tasks: dict[str, asyncio.Task] = {}
    for c in miss_codes:
        t = _inflight.get(c)
        if t is None or t.done():
            t = asyncio.create_task(_fetch_one_sparkline(c))
            _inflight[c] = t
            t.add_done_callback(lambda _t, _c=c: _inflight.pop(_c, None) if _inflight.get(_c) is _t else None)
        tasks[c] = t

    await asyncio.wait(set(tasks.values()), timeout=_BATCH_FETCH_BUDGET)
    pending_n = 0
    for c, t in tasks.items():
        if t.done():
            try:
                v = t.result()
            except Exception:
                v = None
            if v is not None:
                result[c] = v
        else:
            pending_n += 1
    if pending_n:
        logger.info(f"[batch_intraday] 预算{_BATCH_FETCH_BUDGET}s内 {pending_n}/{len(miss_codes)} 只未完成, "
                    "留后台焐缓存待下轮轮询补全")
    return result
