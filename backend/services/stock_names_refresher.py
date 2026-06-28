# -*- coding: utf-8 -*-
"""全市场股票名称刷新服务 - v1.7.x.

来源: 新浪批量行情 hq.sinajs.cn (一次返回名称, 复用 fetcher.quotes._get_quotes_sina; 严禁东财, 生产IP被封).
取全市场代码 = cfzy_sys_kline_cache DISTINCT code (本地全A日线库), 分批拉名 upsert 进 cfzy_sys_stock_names.

调用方:
  - 定时任务 refresh_stock_names (每日07:30 交易日前刷新), 见 task_registry / database 增量种子.
  - main.py lifespan: 空表时 asyncio.create_task 非阻塞首填(不卡启动).
"""
import asyncio
import logging

from backend.fetcher.quotes import _get_quotes_sina
from backend.models import repository
from backend.models.repo._db import _fetchall

logger = logging.getLogger(__name__)

BATCH = 60          # 新浪批量行情每批代码数(单 URL 不宜过长)
PAUSE = 0.05        # 批间轻微退避, 防风控


async def _all_market_codes() -> list[str]:
    """全市场代码: 优先 kline_cache DISTINCT code; 空则退回 5m 表。"""
    rows = await _fetchall("SELECT DISTINCT code FROM cfzy_sys_kline_cache")
    codes = [str(r["code"]) for r in rows if r.get("code")]
    if not codes:
        rows = await _fetchall("SELECT DISTINCT code FROM cfzy_sys_kline_5m")
        codes = [str(r["code"]) for r in rows if r.get("code")]
    return sorted(set(codes))


async def refresh_stock_names() -> dict:
    """拉全市场名称写 cfzy_sys_stock_names。返回 {'codes':N,'named':M,'written':W}。"""
    codes = await _all_market_codes()
    total = len(codes)
    if not total:
        logger.warning("[stock_names] 全市场代码为空(kline 库未填充?), 跳过")
        return {"codes": 0, "named": 0, "written": 0}

    named = 0
    written = 0
    for k in range(0, total, BATCH):
        part = codes[k:k + BATCH]
        try:
            quotes = await _get_quotes_sina(part)
        except Exception as e:
            logger.warning(f"[stock_names] 新浪批量取名失败 batch@{k}: {e}")
            quotes = {}
        rows = []
        for code in part:
            q = quotes.get(code)
            nm = (q.get("name") if q else "") or ""
            nm = nm.strip()
            if nm:
                rows.append((code, nm[:32]))
        if rows:
            named += len(rows)
            try:
                await repository.upsert_stock_names(rows)
                written += len(rows)
            except Exception as e:
                logger.warning(f"[stock_names] upsert 失败 batch@{k}: {e}")
        if PAUSE:
            await asyncio.sleep(PAUSE)

    logger.info(f"[stock_names] 刷新完成 codes={total} named={named} written={written}")
    return {"codes": total, "named": named, "written": written}


async def ensure_stock_names_seeded():
    """启动钩子用: 仅当表为空时才全量首填(已填充则跳过, 留给定时任务增量刷)。非阻塞由调用方 create_task 保证。"""
    try:
        n = await repository.count_stock_names()
    except Exception as e:
        logger.warning(f"[stock_names] 计数失败, 跳过首填: {e}")
        return
    if n > 0:
        logger.info(f"[stock_names] 名称表已有 {n} 条, 跳过启动首填")
        return
    logger.info("[stock_names] 名称表为空, 启动后台一次性首填...")
    try:
        await refresh_stock_names()
    except Exception as e:
        logger.warning(f"[stock_names] 启动首填异常: {e}")
