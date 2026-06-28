"""信号前向逐日表现冻结 (v1.7.x).

目的:
  回测/成功率分析依赖的 K 线缓存只为当前股票池保温, 个股移出池后停更, 时间一长
  历史信号的后续走势数据就丢了。本任务每晚把每个信号触发后 T+1..T+N 的逐日表现
  (相对触发价的 当日最高/最低/收盘 收益) 写死进 cfzy_biz_signal_perf, 一旦冻结永不丢。
  同时顺带刷新所有"近期有信号"的个股 K 线 (get_daily_kline 会回写缓存), 保住原始 K 线。

存 raw 收益 (相对 entry = 触发价); 买/卖点的成败语义 (卖点跌才算赢) 留分析层翻转。
幂等: INSERT IGNORE, PK=(signal_pk, day_offset), 已冻结的日不会重复写。
"""
import asyncio
import logging
from datetime import datetime

from backend import data_fetcher
from backend.models import repository

logger = logging.getLogger(__name__)

PERF_MAX_DAYS = 30        # 冻结 T+1..T+30
CAPTURE_AGE_DAYS = 50     # 触发后 ≤50 自然日(≈30 交易日+buffer) 仍在捕获窗口
REFRESH_CONCURRENCY = 6   # K 线刷新并发上限


async def _fetch_klines(codes: list[str]) -> dict[str, list[dict]]:
    """对每个 code 拉 80 日 K 线 (回写缓存 = 保鲜原始 K 线), 返回 {code: [bar...]}.
    bar = {date, high, low, close}. 拉失败的 code 不在结果里。"""
    sem = asyncio.Semaphore(REFRESH_CONCURRENCY)
    result: dict[str, list[dict]] = {}

    async def _one(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=80)
            except Exception as e:
                logger.warning(f"[perf_snapshot] 拉 K 线失败({code}): {e}")
                return
        if df is None or df.empty:
            return
        result[code] = [
            {"date": str(row["date"]), "high": float(row["high"]),
             "low": float(row["low"]), "close": float(row["close"])}
            for _, row in df.iterrows()
        ]

    await asyncio.gather(*(_one(c) for c in codes))
    return result


async def snapshot_signal_perf() -> dict:
    """主入口: 给捕获窗口内的信号冻结 T+1..T+30 逐日表现。

    Returns: {signals: int, codes: int, rows_inserted: int}
    """
    rows = await repository.fetch_signals_for_perf(max_age_days=CAPTURE_AGE_DAYS)
    if not rows:
        logger.info("[perf_snapshot] 无待捕获信号")
        return {"signals": 0, "codes": 0, "rows_inserted": 0}

    codes = list({r["code"] for r in rows if r.get("code")})
    code_kl = await _fetch_klines(codes)

    now = datetime.now()
    inserts: list[tuple] = []
    for r in rows:
        code = r.get("code")
        entry = float(r.get("price") or 0)
        triggered_at = r.get("triggered_at")
        if not code or entry <= 0 or not triggered_at:
            continue
        bars = code_kl.get(code, [])
        td = str(triggered_at)[:10]
        future = [b for b in bars if b["date"] > td]
        for n in range(1, min(PERF_MAX_DAYS, len(future)) + 1):
            b = future[n - 1]
            inserts.append((
                r["id"], n,
                round((b["high"] - entry) / entry * 100, 2),
                round((b["low"] - entry) / entry * 100, 2),
                round((b["close"] - entry) / entry * 100, 2),
                now,
            ))

    written = await repository.bulk_insert_signal_perf(inserts)
    logger.info(
        f"[perf_snapshot] 信号 {len(rows)} 条 / code {len(codes)} 只, "
        f"待写 {len(inserts)} 行, 实际新增 {written} 行"
    )
    return {"signals": len(rows), "codes": len(codes), "rows_inserted": written or 0}
