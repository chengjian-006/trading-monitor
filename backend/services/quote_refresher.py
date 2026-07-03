import asyncio
import logging
import time
from datetime import datetime

from backend.core.config import load_config
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)

_rank_cache: dict[str, int] = {}
_rank_cache_ts: float = 0
RANK_CACHE_TTL = 300

# 单轮刷新封顶: 任务每3秒触发(max_instances=1, 不堆积), 但 httpx 超时达15s,
# 一次卡住会连跳好几个周期。封到 2.8s 内, 超时则跳过本轮(保留上轮数据)。
REFRESH_TIMEOUT = 2.8
# v1.7.562: 开盘/午休恢复后的第一轮放宽超时 — 恢复瞬间全池时间戳停在上一时段末(基线陈旧),
#   又常撞源慢/任务惊群, 2.8s 连续作废会让全池长时间刷不上(0703 13:00 自检告警 87/153 陈旧的根因)。
#   第一轮给足预算把整池刷一遍, 之后回到 2.8s 节奏。
RESUME_TIMEOUT = 10.0
# v1.7.562: 行情分块抓取+逐块落库 — 源慢时拿到一块落一块, 超时砍掉的只是没抓完的尾块, 不整轮作废。
CORE_CHUNK = 80

_off_hours_refreshed: bool = False


from backend.core.trading_calendar import is_trading_time as _is_trading_time  # v1.7.x 统一来源


async def _get_rank_map(codes: list[str]) -> dict[str, int]:
    global _rank_cache, _rank_cache_ts
    now = time.time()
    if now - _rank_cache_ts < RANK_CACHE_TTL and _rank_cache:
        return _rank_cache
    rank_map = await data_fetcher.get_popularity_rank_for_codes(codes)
    _rank_cache = rank_map
    _rank_cache_ts = now
    return rank_map


async def refresh_quotes():
    global _off_hours_refreshed

    resume_round = False
    if _is_trading_time():
        # 上一轮还处于盘外状态 → 本轮是开盘/午休恢复的第一轮
        resume_round = _off_hours_refreshed
        _off_hours_refreshed = False
    else:
        if _off_hours_refreshed:
            return
        _off_hours_refreshed = True

    all_stocks = await repository.list_all_stocks()
    if not all_stocks:
        return

    codes = list({s["code"] for s in all_stocks})
    timeout = RESUME_TIMEOUT if resume_round else REFRESH_TIMEOUT
    if resume_round:
        logger.info(f"[quotes] 交易时段恢复第一轮: 放宽超时至 {RESUME_TIMEOUT}s 先整池刷一遍")
    try:
        await asyncio.wait_for(_do_refresh(codes, all_stocks), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[quotes] 本轮刷新超过 {timeout}s 被中止, 跳过(保留上轮行情), 避免卡死周期")


async def _do_refresh(codes: list[str], all_stocks: list[dict]):
    # 1) 价/涨跌幅/成交额(新浪, 快) 分块抓取+逐块立刻落库 —— v1.7.562: 源慢时拿到一块落一块,
    #    单轮超时被中止只损失没抓完的尾块, 不再"整轮作废什么都没落"(0703 13:00 大面积陈旧根因之一)。
    #    整轮抓完把合并结果灌回 5s 进程缓存, 其他消费者(扫描器等)缓存命中率不受分块影响。
    quotes: dict = {}
    for k in range(0, len(codes), CORE_CHUNK):
        part = codes[k:k + CORE_CHUNK]
        q = await data_fetcher.fetch_quotes_uncached(part)
        if q:
            core = [{"code": c, "price": q[c]["price"], "pct_change": q[c]["pct_change"],
                     "amount": q[c]["amount"]} for c in part if q.get(c)]
            if core:
                await repository.batch_update_core_quotes(core)
            quotes.update(q)
    data_fetcher.seed_realtime_cache(quotes)

    # 1.5) 持仓在最热题材板块内的强弱名次: 拿实时涨幅在 60s 快照名单里二分定位(零外部请求)。
    #      紧跟核心行情之后算, 不被后面慢的东财 extra 拖累, 保证每 3s 都刷。
    await _update_board_strength(all_stocks, quotes)

    # 2) 再做慢的: 人气排名 + 东财 extra(换手/量比/涨速/free_cap/行业), best-effort 写富字段
    rank_map = await _get_rank_map(codes)
    extras = await data_fetcher.get_stock_extra(codes)
    ma_map = await _get_ma_batch(codes)

    updates = _build_updates(codes, quotes, extras, rank_map, ma_map)
    _apply_speed_fallback(updates)

    if updates:
        await repository.batch_update_quotes(updates)

    await _sync_names(all_stocks, quotes)


async def _update_board_strength(all_stocks: list[dict], quotes: dict):
    """持仓票: 用实时涨幅在缓存板块名单里插值定名次, 写 board_name/rank/total。纯本地计算。"""
    from backend.services.sector_strength_scanner import compute_board_rank

    hold_codes = {s["code"] for s in all_stocks if s.get("status") == "hold"}
    if not hold_codes:
        return
    board_updates = []
    for code in hold_codes:
        rt = quotes.get(code)
        if not rt:
            continue
        r = compute_board_rank(code, rt.get("pct_change"))
        if r:
            board_updates.append({"code": code, **r})
    if board_updates:
        await repository.batch_update_board_strength(board_updates)


async def _get_ma_batch(codes: list) -> dict:
    """批量算 MA10/MA20/MA60: 每个code取最近60日收盘(最新在前), 返回
    {code: {"ma10","ma20","ma60"}}(数据不足则该项为 None)。供股票池均线位置筛选用。"""
    from backend.models import repository
    if not codes: return {}
    try:
        rows = await repository.fetch_kline_close_batch(codes, 60)
    except Exception:
        return {}

    def _ma(vals, k, floor):
        seg = vals[:k]                       # 最新在前, 取前 k 根 = 最近 k 日
        return sum(seg) / len(seg) if len(seg) >= floor else None

    result = {}
    for code in codes:
        closes = rows.get(code, [])
        if not closes:
            continue
        result[code] = {
            "ma10": _ma(closes, 10, 7),
            "ma20": _ma(closes, 20, 15),
            "ma60": _ma(closes, 60, 40),
        }
    return result


def _build_updates(codes, quotes, extras, rank_map, ma_map=None):
    updates = []
    for code in codes:
        rt = quotes.get(code)
        if rt:
            ex = extras.get(code, {})
            ma = (ma_map or {}).get(code) or {}
            updates.append({
                "code": code,
                "price": rt["price"],
                "pct_change": rt["pct_change"],
                "amount": rt["amount"],
                "speed": _pick_speed(rt.get("speed"), ex.get("speed")),
                "industry": rt.get("industry") or ex.get("industry") or "",
                "volume_ratio": _pick(rt.get("volume_ratio"), ex.get("volume_ratio"), 0),
                "free_cap": _pick(rt.get("free_cap"), ex.get("free_cap"), 0),
                "turnover": _pick(rt.get("turnover"), ex.get("turnover"), 0),
                "popularity_rank": rank_map.get(code),
                "ma20": ma.get("ma20"),
                "ma10": ma.get("ma10"),
                "ma60": ma.get("ma60"),
            })
    return updates


def _pick(*values):
    for v in values:
        if isinstance(v, (int, float)) and v != 0:
            return v
    return 0


def _pick_speed(*values):
    """涨速允许为 0/负数 (是真实值), 不能像其它字段把 0 当缺失. 都没有才返回 None."""
    for v in values:
        if isinstance(v, (int, float)):
            return v
    return None


def _apply_speed_fallback(updates):
    """涨速兜底: 东财 ulist 拿不到 speed 的, 用已缓存分时(同花顺/东财 trends2)自算 5min 涨速.
    纯读 prefetch 焐热的缓存, 不在 3s 高频循环里发网络请求.
    """
    need = [u["code"] for u in updates if u.get("speed") is None]
    if not need:
        return
    try:
        speed_map = data_fetcher.get_cached_sparkline_speed(need)
    except Exception as e:
        logger.debug(f"[quotes] 涨速兜底取分时失败: {e}")
        return
    for u in updates:
        if u.get("speed") is None and u["code"] in speed_map:
            u["speed"] = speed_map[u["code"]]


async def _sync_names(stocks: list[dict], quotes: dict):
    """名称同步: 让存库名与"按代码取的实时行情名"(权威)一致。

    - 空名: 走 search_stock 补全(历史逻辑)。
    - 非空但与实时名不一致: 用实时名纠正(自愈脏导入错配的名字 + 跟上 ST 摘帽/改名)。
      仅在实时名非空且确实不同才写一次; 写入后下轮 stored==live 不再写, 故无抖动。
    """
    for s in stocks:
        code = s["code"]
        stored = (s.get("name") or "").strip()
        live = ((quotes.get(code) or {}).get("name") or "").strip()
        if not stored:
            results = await data_fetcher.search_stock(code)
            if results and results[0].get("name"):
                await repository.update_stock(code, s.get("user_id", 1), name=results[0]["name"])
        elif live and live != stored:
            logger.info(f"[quotes] 名称自愈 {code}: '{stored}' → '{live}'")
            await repository.update_stock(code, s.get("user_id", 1), name=live)


async def refresh_quotes_for_codes(codes: list[str]):
    """立即刷新指定股票的行情数据（导入/新增后调用）"""
    if not codes:
        return

    quotes = await data_fetcher.get_realtime_quotes(codes)
    rank_map = await _get_rank_map(codes)
    extras = await data_fetcher.get_stock_extra(codes)
    ma_map = await _get_ma_batch(codes)

    updates = _build_updates(codes, quotes, extras, rank_map, ma_map)
    _apply_speed_fallback(updates)

    if updates:
        await repository.batch_update_quotes(updates)
