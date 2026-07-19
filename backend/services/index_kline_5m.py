# -*- coding: utf-8 -*-
"""指数 5 分钟 K 线增量入库 (v1.7.692).

盘中每 5 分钟追加上证/深成/创业板指的 5 分钟 K 线到 cfzy_sys_index_kline_5m。
新浪源 datalen 上限 1023 根(≈21 交易日滚动), 所以:
  · 日常增量 append_index_kline_5m(): 取 64 根即可覆盖当日 48 根还有富余, 幂等 upsert;
    盘中反复跑会把当前这根未走完的 bar 反复覆盖成最新值, 收盘后自然定格 —— 这是想要的。
  · 首次/补漏 backfill_index_kline_5m(): 取满 1023 根, 把新浪窗口内能拿的全灌进来。
历史深度受限于源(≈21 交易日), 2 年历史需另找付费源; 本模块负责"从今天起不断积累"。
"""

import asyncio
import logging

import httpx

from backend.fetcher.index_klines import (
    INDEXES, SINA_MAX_DATALEN, IndexKlineError, fetch_index_5m,
)
from backend.models.repo.index_kline_5m import index_kline_coverage, upsert_index_bars

logger = logging.getLogger(__name__)

_FETCH_GAP = 0.8       # 指数间隔, 新浪对连打敏感, 3 个指数拉开跑


async def _run(datalen: int, label: str) -> dict:
    """对 INDEXES 逐个抓取 + upsert; 单个失败不影响其它, 汇总返回。"""
    ok, failed, total = 0, [], 0
    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), trust_env=False)
    try:
        for i, (symbol, name) in enumerate(INDEXES):
            if i:
                await asyncio.sleep(_FETCH_GAP)
            try:
                bars = await fetch_index_5m(symbol, datalen=datalen, client=client)
                if not bars:
                    failed.append(f"{name}(空)")
                    continue
                total += await upsert_index_bars(symbol, bars)
                ok += 1
            except IndexKlineError as e:
                failed.append(f"{name}({e})")
            except Exception as e:      # noqa: BLE001 — 单指数异常不该中断整轮
                failed.append(f"{name}({type(e).__name__})")
    finally:
        await client.aclose()
    if failed:
        logger.warning(f"[index_5m] {label}: 成功 {ok}/{len(INDEXES)}, 失败 {failed}")
    else:
        logger.info(f"[index_5m] {label}: {ok} 个指数, 写入 {total} 根")
    return {"ok": ok, "bars": total, "failed": failed}


async def append_index_kline_5m():
    """定时入口(盘中每5分钟): 增量追加指数 5 分钟 K 线。

    非交易时段直接跳过 —— 交由 TaskSkipped 让调度器记 skipped 而非 success,
    避免"从没成功过"和"跳过"混为一谈。
    """
    from backend.core.task_signals import TaskSkipped
    from backend.core.trading_calendar import is_trading_time

    if not is_trading_time():
        raise TaskSkipped("非交易时段")
    return await _run(datalen=64, label="盘中增量")


async def backfill_index_kline_5m(datalen: int = SINA_MAX_DATALEN) -> dict:
    """首次/补漏: 把新浪窗口内(最多 1023 根≈21 交易日)能拿的全部灌入。"""
    res = await _run(datalen=datalen, label=f"回填 datalen={datalen}")
    res["coverage"] = await index_kline_coverage()
    return res
