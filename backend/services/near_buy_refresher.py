"""临近买点快照刷新 — 短线盯盘 v1.7.x.

每 3 分钟(交易日内 + 盘后一档)扫每个用户的"自选(watch/focused)+持仓(hold)"全池,
算每只票距四买点的接近度(near_buy.evaluate, 触发/接近两档), 整表 UPSERT 到
cfzy_sys_near_buy_snapshot, 前端监控看板"临近买点"面板从此读, 不再实时拉K线。

与实时扫描(scan_stock_pool, 每30s, 仅 focused+hold)的差异:
  - 覆盖面更广: 整个自选池都算接近度(不止重点关注), 因为"接近"本就是给观察用;
  - 频率更低(3min): 接近度是日线结构指标, 不需要30s级刷新, 也省外部行情调用;
  - 不推送: 纯快照供盯盘, 触发推送仍由 scan_stock_pool 负责。
"""
import asyncio
import logging
from datetime import datetime

from backend.core.trading_calendar import is_workday
from backend.models import repository
from backend import data_fetcher
from backend.services import near_buy
from backend.services.signal_engine_config import get_merged_config

logger = logging.getLogger(__name__)

STATUS_LABEL = {"watch": "自选", "hold": "持仓"}


def _in_window(now: datetime) -> bool:
    """采集窗口: 工作日 09:25(集合竞价后)~15:10(收盘后一档), 同情绪快照。"""
    if not is_workday(now):
        return False
    return "09:25" <= now.strftime("%H:%M") <= "15:10"


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


async def _refresh_one_user(user_id: int, stocks: list[dict],
                            quotes: dict, kline_map: dict, cfg: dict) -> None:
    """算单个用户的临近买点榜并落库。"""
    items: list[dict] = []
    scanned = 0
    trade_date = ""
    for s in stocks:
        code = s["code"]
        df = kline_map.get(code)
        if df is None or df.empty or len(df) < 65:
            continue
        scanned += 1
        if not trade_date:
            trade_date = str(df.iloc[-1]["date"])[:10]
        rt = quotes.get(code)
        try:
            res = near_buy.evaluate(df, rt, cfg)
        except Exception as e:
            logger.debug(f"[near_buy] 评估失败 {code}: {e}")
            continue
        if not res:
            continue
        price = float(rt["price"]) if rt and rt.get("price") else float(df.iloc[-1]["close"])
        pct = float(rt.get("pct_change", 0)) if rt else 0.0
        items.append({
            "code": code, "name": s.get("name") or code,
            "status": s.get("status"), "status_label": STATUS_LABEL.get(s.get("status"), ""),
            "price": round(price, 2), "pct": round(pct, 2),
            "tier": res["tier"], "dist": res["dist"], "hits": res["hits"],
        })

    # 触发优先, 再按距均线由近到远
    items.sort(key=lambda it: (-it["tier"], it["dist"]))
    await repository.save_near_buy_snapshot(
        user_id, trade_date or datetime.now().strftime("%Y-%m-%d"), items, scanned)
    logger.info(f"[near_buy] user{user_id} 扫{scanned}只 接近/触发{len(items)}只")


async def refresh_near_buy_snapshot() -> None:
    """定时入口: 按用户聚合自选池 → 共享拉K线/报价 → 逐用户评估落库。"""
    if not _in_window(datetime.now()):
        return

    all_stocks = await repository.list_all_stocks()
    if not all_stocks:
        return

    # 按用户聚合 watch/focused/hold 自选池; 顺带收集去重 codes 统一拉数据
    by_user: dict[int, list[dict]] = {}
    codes: set[str] = set()
    for s in all_stocks:
        if _is_st(s.get("name")):
            continue
        status = s.get("status")
        if not (status in ("watch", "hold") or s.get("focused")):
            continue
        by_user.setdefault(s["user_id"], []).append(s)
        codes.add(s["code"])
    if not codes:
        return

    try:
        quotes = await data_fetcher.get_realtime_quotes(list(codes))
    except Exception as e:
        logger.warning(f"[near_buy] 拉报价失败: {e}")
        quotes = {}

    sem = asyncio.Semaphore(3)

    async def _fetch_kline(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=150)
            except Exception as e:
                logger.warning(f"[near_buy] {code} K线失败: {e}")
                df = None
            await asyncio.sleep(0.3)
            return code, df

    kline_results = await asyncio.gather(*[_fetch_kline(c) for c in codes])
    kline_map = {code: df for code, df in kline_results}

    cfg_cache: dict[int, dict] = {}
    for user_id, stocks in by_user.items():
        if user_id not in cfg_cache:
            user_config = await repository.get_signal_config(user_id)
            cfg_cache[user_id] = get_merged_config(user_config)
        try:
            await _refresh_one_user(user_id, stocks, quotes, kline_map, cfg_cache[user_id])
        except Exception as e:
            logger.error(f"[near_buy] user{user_id} 落库失败: {e}")
