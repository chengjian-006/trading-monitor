"""信号闭环 — 触发后实际收益回填 (v1.7.x).

目的:
  cfzy_biz_signals 只记录"信号触发"事件, 不知道触发后实际表现.
  这个任务每日 23:00 回填一次:
    - p1_pct: 触发日后第 1 个交易日收盘相对 entry_price 的实际收益(%)
    - p3_pct: 第 3 个
    - p5_pct: 第 5 个
    - outcome: 综合判定 success / fail / neutral
  sell/reduce 信号翻转语义: 后续股价下跌 → +盈利(避损成功), 上涨 → -亏损(踏空).

Outcome 判定 (基于 p5_pct, 翻转后语义):
  - success: p5_pct >= +5%   (买点真的涨了 / 卖点真的避开了 5% 跌幅)
  - fail:    p5_pct <= -3%   (买点深套 / 卖点踏空)
  - neutral: 介于之间

依赖:
  cfzy_sys_kline_cache 必须有触发日往后至少 5 个交易日的 K 线数据.
  K 线缓存由日终 quote_refresher / 信号扫描时填充, 通常 T+1 就齐.
"""
import asyncio
import logging
from datetime import datetime

from backend.models import repository

logger = logging.getLogger(__name__)


SUCCESS_THRESHOLD = 5.0   # p5_pct >= +5%  => success
FAIL_THRESHOLD = -3.0     # p5_pct <= -3%  => fail
EVAL_WINDOW_DAYS = 5      # 触发后至少 5 个交易日才评估
REFRESH_CONCURRENCY = 5   # 实时补拉 K 线的并发上限


async def _refresh_klines_for_codes(codes: set[str]) -> dict[str, list[dict]]:
    """对缓存 K 线不足的 code 实时补拉 (新浪→东财→同花顺, 自动回写缓存).

    解决「信号触发后个股被移出股票池 → refresher 不再更新其 K 线 → 触发后凑不满
    5 个交易日 → outcome 永远卡 pending」的结构性缺口.
    返回 {code: [{"trade_date": ..., "close": ...}, ...]}, 拉失败的 code 不在结果里.
    """
    from backend import data_fetcher

    sem = asyncio.Semaphore(REFRESH_CONCURRENCY)
    result: dict[str, list[dict]] = {}

    async def _one(code: str):
        async with sem:
            try:
                df = await data_fetcher.get_daily_kline(code, days=150)
            except Exception as e:
                logger.warning(f"[backfill_outcomes] 补拉 K 线失败({code}): {e}")
                return
        if df is None or df.empty:
            logger.warning(f"[backfill_outcomes] 补拉 K 线为空({code}), 跳过")
            return
        result[code] = [
            {"trade_date": str(row["date"]), "close": row["close"]}
            for _, row in df.iterrows()
        ]

    await asyncio.gather(*(_one(c) for c in codes))
    return result


def _judge_outcome(p5_pct: float | None) -> str | None:
    """根据第 5 日收盘收益(已翻转) 判定 outcome."""
    if p5_pct is None:
        return None
    if p5_pct >= SUCCESS_THRESHOLD:
        return "success"
    if p5_pct <= FAIL_THRESHOLD:
        return "fail"
    return "neutral"


async def backfill_signal_outcomes() -> dict:
    """主入口: 选未评估且触发后已≥5 个交易日的信号, 算 1/3/5 日收益 + outcome 写回.

    Returns: {processed: int, success: int, fail: int, neutral: int, skipped: int}
    """
    # 选触发后已"足够久"的未评估信号 (至少 7 自然日, 保证 5 个交易日已完成 + K 线缓存就位)
    rows = await repository.fetch_signals_pending_outcome(min_age_days=7)
    if not rows:
        logger.info("[backfill_outcomes] 没有待评估信号")
        return {"processed": 0, "success": 0, "fail": 0, "neutral": 0, "skipped": 0}

    # 一次性 IN 批拉 K 线缓存, 避免 N+1
    codes = list({r["code"] for r in rows if r.get("code")})
    if not codes:
        return {"processed": 0, "success": 0, "fail": 0, "neutral": 0, "skipped": 0}

    code_kline_map = await repository.fetch_kline_cache_for_codes(
        codes,
        min_trade_date=min(str(r["triggered_at"])[:10] for r in rows if r.get("triggered_at")),
    )

    # 这些信号都已 ≥7 天 (fetch_signals_pending_outcome 过滤), 理应已有 ≥5 个交易日.
    # 若缓存里触发后 K 线 < 5 根, 说明该 code 已被移出股票池、refresher 停更 → 实时补拉.
    need_refresh: set[str] = set()
    for r in rows:
        code = r.get("code")
        triggered_at = r.get("triggered_at")
        if not code or not triggered_at:
            continue
        td = str(triggered_at)[:10]
        future = [k for k in code_kline_map.get(code, []) if str(k["trade_date"]) > td]
        if len(future) < EVAL_WINDOW_DAYS:
            need_refresh.add(code)
    if need_refresh:
        logger.info(f"[backfill_outcomes] {len(need_refresh)} 个 code 缓存 K 线不足, 实时补拉")
        refreshed = await _refresh_klines_for_codes(need_refresh)
        code_kline_map.update(refreshed)

    counters = {"processed": 0, "success": 0, "fail": 0, "neutral": 0, "skipped": 0}
    now = datetime.now()
    updates: list[tuple] = []

    for r in rows:
        code = r.get("code")
        entry = float(r.get("price") or 0)
        triggered_at = r.get("triggered_at")
        if not code or entry <= 0 or not triggered_at:
            counters["skipped"] += 1
            continue
        kls = code_kline_map.get(code, [])
        td = str(triggered_at)[:10]
        future = [k for k in kls if str(k["trade_date"]) > td]
        if len(future) < EVAL_WINDOW_DAYS:
            # K 线缓存不全, 留到明天再试 (不写 outcome_evaluated_at)
            counters["skipped"] += 1
            continue

        direction = str(r.get("direction") or "").lower()
        flip = -1.0 if direction in ("sell", "reduce") else 1.0

        def close_pct(n: int) -> float | None:
            if len(future) < n:
                return None
            c = float(future[n - 1].get("close") or 0)
            if c <= 0:
                return None
            return round(flip * (c - entry) / entry * 100, 2)

        p1 = close_pct(1)
        p3 = close_pct(3)
        p5 = close_pct(5)
        outcome = _judge_outcome(p5)
        if outcome is None:
            counters["skipped"] += 1
            continue

        updates.append((p1, p3, p5, outcome, now, r["id"]))
        counters[outcome] += 1
        counters["processed"] += 1

    if updates:
        await repository.bulk_update_signal_outcome(updates)

    logger.info(
        f"[backfill_outcomes] 共评估 {counters['processed']} 条 "
        f"(success={counters['success']} fail={counters['fail']} "
        f"neutral={counters['neutral']} skipped={counters['skipped']})"
    )
    return counters
