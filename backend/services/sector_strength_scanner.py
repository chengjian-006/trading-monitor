"""持仓在最热题材板块内的强弱名次 — refresh_sector_strength。

设计(快照解耦 + 实时插值): 把"重活"与"快活"拆开, 既保住盯盘的 3s 实时手感, 又不打爆东财。
  - 重活(本任务, 60s 一次): 只针对当前持仓涉及的"最热题材"板块, 各拉一次全成分股涨幅名单,
    在内存缓存成 {bk_code: 已按涨幅降序的数组}, 并定出每只持仓的最热题材(今日涨幅最高的概念板块)。
    持仓通常就十几只、去重后板块更少, 东财调用 ≈ 每分钟一轮, 可控。
  - 快活(quote_refresher 每 3s): 拿持仓自己的实时涨幅去缓存名单里二分定位, 算名次/总数/分位,
    不发任何外部请求。于是你的票一动, 板块内名次 3s 内跟着变; 只有同板块其他票的位置是 ≤60s 前快照。

只在交易窗口(工作日 09:25~15:10)内刷新; 窗口外保留上一轮缓存。东财拉名单失败的板块该票留空(不杜撰)。
"""
import asyncio
import bisect
import logging
import time
from datetime import datetime

import httpx

from backend.core.trading_calendar import is_workday
from backend.fetcher.sectors import get_stock_concepts, get_concept_板块_quotes, get_board_all_pct
from backend.models import repository

logger = logging.getLogger(__name__)

# 全局硬上限: 整轮重活封顶, 任何挂起都自我了断, 不拖累实时行情。
REFRESH_HARD_TIMEOUT = 20.0

# 专用 HTTP 客户端(独立小连接池 + 短超时): 与主行情共享池隔离, 即便东财慢/被封,
# 也最多占用本池 4 条连接、各自 4s 超时, 绝不会饿死每 3s 的实时报价刷新(主池 20 连接)。
_client: httpx.AsyncClient | None = None


def _get_strength_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=3.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            follow_redirects=True,
            trust_env=False,
        )
    return _client

# 内存缓存(进程级, 重启即空, 下一轮 60s 任务回填):
#   _board_pct[bk_code]  = 该板块全成分股当日涨幅, 升序(便于 bisect 求名次)
#   _code_board[code]    = {"name": 题材名, "bk_code": bk}  持仓的最热题材板块
#   _snapshot_ts         = 上一轮快照时间戳, 给前端/诊断判新鲜度
_board_pct: dict[str, list[float]] = {}
_code_board: dict[str, dict] = {}
_snapshot_ts: float = 0.0


def _in_window(now: datetime) -> bool:
    if not is_workday(now):
        return False
    return "09:25" <= now.strftime("%H:%M") <= "15:10"


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


def compute_board_rank(code: str, live_pct: float | None) -> dict | None:
    """纯计算(零外部请求): 用持仓实时涨幅在缓存的板块名单里定位名次。

    返回 {board_name, board_rank, board_total}; 无缓存/无最热题材 → None。
    名次 = 板块内涨幅严格高于本票的成分股数 + 1(名单含本票的快照旧值, 至多差 1 位, 盯盘可忽略)。
    """
    if live_pct is None:
        return None
    board = _code_board.get(code)
    if not board:
        return None
    arr = _board_pct.get(board["bk_code"])
    if not arr:
        return None
    total = len(arr)
    # arr 升序: 高于 live_pct 的个数 = total - 右边界插入点
    ahead = total - bisect.bisect_right(arr, live_pct)
    rank = min(ahead + 1, total)
    return {"board_name": board["name"], "board_rank": rank, "board_total": total}


async def refresh_sector_strength() -> None:
    """60s 任务: 重建持仓最热题材板块的成分名单缓存。整轮硬封顶, 走隔离客户端。"""
    if not _in_window(datetime.now()):
        return

    all_stocks = await repository.list_all_stocks()
    hold_codes = sorted({s["code"] for s in all_stocks
                         if s.get("status") == "hold" and not _is_st(s.get("name"))})
    if not hold_codes:
        _board_pct.clear()
        _code_board.clear()
        return

    try:
        await asyncio.wait_for(_do_refresh(hold_codes), timeout=REFRESH_HARD_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"[sector_strength] 本轮超 {REFRESH_HARD_TIMEOUT}s 被中止, 保留上轮缓存(隔离池, 不影响实时行情)")
    except Exception as e:
        logger.warning(f"[sector_strength] 本轮失败, 保留上轮缓存: {e}")


async def _do_refresh(hold_codes: list[str]) -> None:
    global _snapshot_ts
    client = _get_strength_client()

    # 1) 持仓 → 概念题材名 + (题材名 → BK code) [隔离客户端]
    concepts_map, bk_map = await get_stock_concepts(hold_codes, client=client)
    if not bk_map:
        return

    # 2) 各概念板块今日涨幅 → 给每只持仓选"最热题材"(涨幅最高的板块); 不要 5 日 K 线, 省请求
    board_quotes = await get_concept_板块_quotes(bk_map, client=client, with_5day=False)
    if not board_quotes:
        return

    code_board: dict[str, dict] = {}
    wanted_bk: dict[str, str] = {}  # bk_code -> 题材名(去重, 待拉名单)
    for code in hold_codes:
        names = [n for n in concepts_map.get(code, []) if n in board_quotes]
        if not names:
            continue
        hottest = max(names, key=lambda n: board_quotes[n].get("pct_today", 0) or 0)
        bk = board_quotes[hottest].get("bk_code") or bk_map.get(hottest, "")
        if not bk:
            continue
        code_board[code] = {"name": hottest, "bk_code": bk}
        wanted_bk[bk] = hottest

    if not wanted_bk:
        return

    # 3) 去重后逐个板块拉全成分股涨幅名单, 存升序数组(给 bisect 求名次) [隔离客户端]
    sem = asyncio.Semaphore(3)

    async def _fetch(bk: str):
        async with sem:
            pcts = await get_board_all_pct(bk, client=client)
            return bk, sorted(pcts)

    results = await asyncio.gather(*[_fetch(bk) for bk in wanted_bk])
    new_board_pct = {bk: arr for bk, arr in results if arr}

    # 丢掉拉名单失败的板块对应的持仓映射(该票本轮留空, 不用过期数据)
    _code_board.clear()
    _code_board.update({c: b for c, b in code_board.items() if b["bk_code"] in new_board_pct})
    _board_pct.clear()
    _board_pct.update(new_board_pct)
    _snapshot_ts = time.time()
    logger.info(f"[sector_strength] 持仓{len(hold_codes)}只 命中板块{len(_board_pct)}个 映射{len(_code_board)}只")
