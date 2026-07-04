"""信号 CRUD + 历史 + outcome 回填 + 摸高/收盘 perf 计算 - cfzy_biz_signals 表.

主要分组:
  save_signal / get_today_signals / get_today_signals_all / signal_already_sent_today
  get_signals_history / get_signals_history_with_perf  -- 5/10/20 日 max + close 收益
  fetch_signals_pending_outcome / bulk_update_signal_outcome  -- 闭环回填
  get_signal_outcome_stats  -- 按 signal_id 真实胜率聚合 (close-based)
  get_signal_stats          -- 按 signal_id 摸高胜率聚合 (high-based, 天花板)
  fetch_kline_cache_for_codes  -- IN 批拉 K 线缓存, 给 perf 计算复用
"""
from datetime import date, timedelta

from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone
from backend.services.review_metrics import (
    build_category_where, extract_trade_plan, parse_exit_plan, compute_kline_returns,
    returns_from_perf, returns_from_outcome, summarize_rows,
)


async def save_signal(code: str, name: str, signal_id: str, signal_name: str,
                      direction: str, price: float, detail: str = "", user_id: int = 1,
                      indicators: dict | None = None,
                      signal_group: str = ""):
    """v1.7.x: 使用 INSERT IGNORE 配合 uk_signal_day(code, signal_id, user_id, trigger_date) 唯一索引,
    DB 层兜底防止并发场景下"同日同 code+signal_id"重复写入。
    若迁移加索引失败(如生产有历史重复), IGNORE 不会报错也不会跳过, 退化为应用层 signal_already_sent_today 防重。

    signal_group: 信号分组(entry/exit/risk/regime/sector/quality), 取自 signal_specs.group_of().
    """
    import json
    ind_json = json.dumps(indicators, ensure_ascii=False) if indicators else None
    await _execute(
        "INSERT IGNORE INTO cfzy_biz_signals (code, name, signal_id, signal_name, direction, price, detail, user_id, indicators, signal_group) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (code, name, signal_id, signal_name, direction, price, detail, user_id, ind_json, signal_group),
    )


async def get_today_signals(user_id: int = 1, code: str = None) -> list[dict]:
    # trigger_date 生成列 + idx_trigger_date 索引; DATE(triggered_at) 函数包列会全表扫
    if code:
        return await _fetchall(
            "SELECT * FROM cfzy_biz_signals WHERE trigger_date = CURDATE() AND code = %s AND user_id = %s "
            "ORDER BY triggered_at DESC",
            (code, user_id),
        )
    return await _fetchall(
        "SELECT * FROM cfzy_biz_signals WHERE trigger_date = CURDATE() AND user_id = %s ORDER BY triggered_at DESC",
        (user_id,),
    )


async def get_today_signals_all() -> list[dict]:
    return await _fetchall(
        "SELECT * FROM cfzy_biz_signals WHERE trigger_date = CURDATE() ORDER BY triggered_at DESC"
    )


async def set_eod_audit(signal_pk: int, status: str, note: str = ""):
    """v1.7.387 EOD自动复核结果: status='ok'/'suspect'/'unverified'."""
    await _execute(
        "UPDATE cfzy_biz_signals SET eod_audit = %s, eod_audit_note = %s WHERE id = %s",
        (status, note, signal_pk),
    )


async def get_signals_by_code_date(code: str, user_id: int = 1, date: str | None = None) -> list[dict]:
    """某票某交易日的买卖点(给分时图标记用)。date 为空取当日。按时间正序。"""
    if date:
        return await _fetchall(
            "SELECT signal_id, signal_name, direction, price, triggered_at FROM cfzy_biz_signals "
            "WHERE code = %s AND user_id = %s AND trigger_date = %s ORDER BY triggered_at ASC",
            (code, user_id, date),
        )
    return await _fetchall(
        "SELECT signal_id, signal_name, direction, price, triggered_at FROM cfzy_biz_signals "
        "WHERE code = %s AND user_id = %s AND trigger_date = CURDATE() ORDER BY triggered_at ASC",
        (code, user_id),
    )


async def get_signals_by_code_since(code: str, user_id: int = 1, days: int = 150) -> list[dict]:
    """某票近 N 天的买卖点(给日K图标记)。按触发时间正序。"""
    return await _fetchall(
        "SELECT signal_name, direction, price, triggered_at FROM cfzy_biz_signals "
        "WHERE code = %s AND user_id = %s AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY triggered_at ASC",
        (code, user_id, days),
    )


async def get_stop_fires_by_code(code: str, signal_ids: list[str], user_id: int = 1,
                                 days: int = 30) -> list[dict]:
    """某票近 N 天内指定硬止损 signal_id 的触发记录(止损强制升级用)。
    返回 [{signal_id, price, d:'YYYY-MM-DD', triggered_at}], 按触发时间正序(最早在前)。"""
    if not signal_ids:
        return []
    ph = ", ".join(["%s"] * len(signal_ids))
    return await _fetchall(
        f"SELECT signal_id, price, DATE(triggered_at) AS d, triggered_at FROM cfzy_biz_signals "
        f"WHERE code = %s AND user_id = %s AND signal_id IN ({ph}) "
        f"AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        f"ORDER BY triggered_at ASC",
        (code, user_id, *signal_ids, days),
    )


async def get_key_signals_between(start: str, end: str, directions: list[str]) -> list[dict]:
    """时间窗 [start, end] 内指定方向的关键信号(全用户), 按触发时间正序。给推送补发用。
    同票同方向窗口内只取首次(GROUP BY 去掉重复触发)。"""
    if not directions:
        return []
    placeholders = ", ".join(["%s"] * len(directions))
    return await _fetchall(
        f"SELECT MIN(triggered_at) AS triggered_at, code, name, direction, signal_name "
        f"FROM cfzy_biz_signals "
        f"WHERE triggered_at >= %s AND triggered_at <= %s AND direction IN ({placeholders}) "
        f"GROUP BY code, name, direction, signal_name "
        f"ORDER BY triggered_at ASC",
        (start, end, *directions),
    )


async def get_signal_days_for_code(code: str, user_id: int = 1, limit: int = 30) -> list[str]:
    """某票有买卖点的交易日列表(倒序), 给分时图历史回放的日期选择器。"""
    rows = await _fetchall(
        "SELECT trigger_date AS d, COUNT(*) AS cnt FROM cfzy_biz_signals "
        "WHERE code = %s AND user_id = %s GROUP BY trigger_date ORDER BY d DESC LIMIT %s",
        (code, user_id, limit),
    )
    return [str(r["d"]) for r in rows]


def _history_where(user_id: int, date: str | None, start_date: str | None, end_date: str | None,
                   signal_id: str | None = None):
    """构造历史信号 WHERE 子句 + 参数。date 精确单日(优先); 否则按 start/end 闭区间; 可加 signal_id 过滤。"""
    where = "user_id = %s"
    params: list = [user_id]
    if signal_id:
        where += " AND signal_id = %s"
        params.append(signal_id)
    if date:
        where += " AND trigger_date = %s"
        params.append(date)
    else:
        if start_date:
            where += " AND trigger_date >= %s"
            params.append(start_date)
        if end_date:
            where += " AND trigger_date <= %s"
            params.append(end_date)
    return where, params


async def get_signals_history(user_id: int = 1, limit: int = 200, date: str | None = None,
                              start_date: str | None = None, end_date: str | None = None,
                              signal_id: str | None = None) -> list[dict]:
    where, params = _history_where(user_id, date, start_date, end_date, signal_id)
    return await _fetchall(
        f"SELECT * FROM cfzy_biz_signals WHERE {where} ORDER BY triggered_at DESC LIMIT %s",
        (*params, limit),
    )


async def signal_already_sent_today(code: str, signal_id: str, user_id: int = 1) -> bool:
    # trigger_date 是 DATE(triggered_at) 的生成列, 命中 uk_signal_day 唯一索引;
    # 直接 DATE(triggered_at)=CURDATE() 会让索引失效, 故走生成列。
    row = await _fetchone(
        "SELECT COUNT(*) AS cnt FROM cfzy_biz_signals "
        "WHERE code = %s AND signal_id = %s AND user_id = %s AND trigger_date = CURDATE()",
        (code, signal_id, user_id),
    )
    return row["cnt"] > 0 if row else False


async def buy_signal_already_sent_today(code: str, user_id: int = 1) -> bool:
    """当日该股是否已有过任意买点 (跨买点去重: 一股一天只推一个买点, 减噪)。"""
    row = await _fetchone(
        "SELECT COUNT(*) AS cnt FROM cfzy_biz_signals "
        "WHERE code = %s AND direction = 'buy' AND user_id = %s AND trigger_date = CURDATE()",
        (code, user_id),
    )
    return row["cnt"] > 0 if row else False


async def get_sent_signal_keys_today(user_ids: list[int]) -> list[dict]:
    """一次性取今日(各 user)已推信号键, 供扫描循环内存去重, 替代逐信号 N+1 查询。"""
    if not user_ids:
        return []
    placeholders = ",".join(["%s"] * len(user_ids))
    return await _fetchall(
        f"SELECT user_id, code, signal_id, direction FROM cfzy_biz_signals "
        f"WHERE user_id IN ({placeholders}) AND trigger_date = CURDATE()",
        tuple(user_ids),
    )


async def fetch_kline_cache_for_codes(codes: list[str], min_trade_date: str) -> dict[str, list[dict]]:
    """一次性批拉指定 codes 在 min_trade_date 之后的 K 线 (date+open+high+low+close).

    给信号 perf 计算 / outcome 回填等多个调用方复用, 避免 N+1 查询.
    """
    result: dict[str, list[dict]] = {c: [] for c in codes}
    if not codes:
        return result
    placeholders = ",".join(["%s"] * len(codes))
    rows = await _fetchall(
        f"SELECT code, trade_date, open, high, low, close FROM cfzy_sys_kline_cache "
        f"WHERE code IN ({placeholders}) AND trade_date > %s "
        f"ORDER BY code, trade_date ASC",
        (*codes, min_trade_date),
    )
    for r in rows:
        result[r["code"]].append(r)
    return result


async def get_signals_history_with_perf(user_id: int = 1, limit: int = 200, date: str | None = None,
                                        start_date: str | None = None, end_date: str | None = None,
                                        signal_id: str | None = None) -> list[dict]:
    """信号历史 + 触发后 +5d/+10d/+20d 的最高涨幅 + 收盘收益 + 当前价 (v1.7.39, v1.7.x close 视角)."""
    where, params = _history_where(user_id, date, start_date, end_date, signal_id)
    rows = await _fetchall(
        f"SELECT * FROM cfzy_biz_signals WHERE {where} ORDER BY triggered_at DESC LIMIT %s",
        (*params, limit),
    )
    if not rows:
        return []

    # 一次性 IN 批拉, 按全局最早 triggered_at 限制 trade_date 范围 (v1.7.68)
    codes = list({r["code"] for r in rows if r.get("code")})
    code_kline_map: dict[str, list[dict]] = {c: [] for c in codes}
    if codes:
        min_td = min(str(r["triggered_at"])[:10] for r in rows if r.get("triggered_at"))
        placeholders = ",".join(["%s"] * len(codes))
        kls_all = await _fetchall(
            f"SELECT code, trade_date, high, close FROM cfzy_sys_kline_cache "
            f"WHERE code IN ({placeholders}) AND trade_date > %s "
            f"ORDER BY code, trade_date ASC",
            (*codes, min_td),
        )
        for k in kls_all:
            code_kline_map[k["code"]].append(k)

    for row in rows:
        code = row.get("code")
        triggered_at = row.get("triggered_at")
        entry_price = float(row.get("price") or 0)
        if not code or not triggered_at or entry_price <= 0:
            row["perf"] = None
            continue
        td_str = str(triggered_at)[:10]
        kls = code_kline_map.get(code, [])
        future = [k for k in kls if str(k["trade_date"]) > td_str]
        if not future:
            row["perf"] = None
            continue

        # 卖/减仓信号的 "盈亏" = 卖后避开的跌幅, 故对 close-based 字段取负
        direction = str(row.get("direction") or "").lower()
        flip = -1.0 if direction in ("sell", "reduce") else 1.0

        def max_pct(n: int) -> float | None:
            seg = future[:n]
            if not seg:
                return None
            mh = max(float(k["high"] or 0) for k in seg)
            if mh <= 0:
                return None
            return round((mh - entry_price) / entry_price * 100, 2)

        def close_pct(n: int) -> float | None:
            if len(future) < n:
                return None
            cn = float(future[n - 1].get("close") or 0)
            if cn <= 0:
                return None
            return round(flip * (cn - entry_price) / entry_price * 100, 2)

        last_close = float(future[-1]["close"] or 0) if future[-1].get("close") else 0
        current_pct = round(flip * (last_close - entry_price) / entry_price * 100, 2) if last_close > 0 else None

        row["perf"] = {
            "p5_max": max_pct(5),
            "p10_max": max_pct(10),
            "p20_max": max_pct(20),
            "p5_close": close_pct(5),
            "p10_close": close_pct(10),
            "p20_close": close_pct(20),
            "current_pct": current_pct,
            "elapsed_days": len(future),
        }
    return rows


# ── 信号闭环: outcome 回填 helpers (v1.7.x) ──

async def fetch_signals_pending_outcome(min_age_days: int = 7) -> list[dict]:
    """选触发后已≥min_age_days 天、outcome 还未评估的信号 (所有 user 一起处理)."""
    return await _fetchall(
        "SELECT id, code, signal_id, direction, price, triggered_at "
        "FROM cfzy_biz_signals "
        "WHERE outcome_evaluated_at IS NULL "
        "AND triggered_at <= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY triggered_at ASC LIMIT 5000",
        (min_age_days,),
    )


async def bulk_update_signal_outcome(updates: list[tuple]) -> None:
    """批量写回 outcome 字段. updates: [(p1, p3, p5, outcome, evaluated_at, id), ...]"""
    if not updates:
        return
    await _executemany(
        "UPDATE cfzy_biz_signals SET "
        "outcome_p1_pct = %s, outcome_p3_pct = %s, outcome_p5_pct = %s, "
        "outcome = %s, outcome_evaluated_at = %s WHERE id = %s",
        updates,
    )


# ── 信号前向逐日表现冻结 (cfzy_biz_signal_perf) ──

async def fetch_signals_for_perf(max_age_days: int = 50) -> list[dict]:
    """选需要捕获前向表现的信号: 个股买卖点(direction 买/卖/减 + 6位数字代码)、有触发价、
    已≥1天(T+1已存在)、且触发后未超 max_age_days (30 交易日 ≈ 42 自然日, 留 buffer)。
    排除大盘急跌(code=指数)/板块(code=BKxxxx)等非个股信号。全 user 一起处理。"""
    return await _fetchall(
        "SELECT id, code, price, direction, triggered_at FROM cfzy_biz_signals "
        "WHERE price > 0 "
        "AND direction IN ('buy', 'sell', 'reduce') "
        "AND code REGEXP '^[0-9]{6}$' "
        "AND triggered_at <= DATE_SUB(NOW(), INTERVAL 1 DAY) "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY triggered_at ASC LIMIT 10000",
        (max_age_days,),
    )


async def bulk_insert_signal_perf(rows: list[tuple]) -> int:
    """批量写入逐日前向表现 (幂等 INSERT IGNORE, PK=signal_pk+day_offset).
    rows: [(signal_pk, day_offset, high_pct, low_pct, close_pct, captured_at), ...]
    返回实际写入行数。"""
    if not rows:
        return 0
    return await _executemany(
        "INSERT IGNORE INTO cfzy_biz_signal_perf "
        "(signal_pk, day_offset, high_pct, low_pct, close_pct, captured_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        rows,
    )


async def get_signal_perf_stats(user_id: int = 1, days_back: int = 120) -> list[dict]:
    """真实信号逐日成功率: 读 cfzy_biz_signal_perf(已冻结的实盘信号前向表现), 按
    signal_id × day_offset 聚合。方向翻转: 卖/减点价跌才算赢, 故收益 * -1。

    返回 [{signal_id, signal_name, direction, trigger_count,
           curve: [{day, samples, avg_return, avg_best, avg_worst,
                    up_rate, win_rate, loss_rate}]}], 按 signal_id 排序。
    avg_return/best/worst 均为"信号视角"收益(已翻转); 胜=收益≥+5%, 负=收益≤-3%。
    """
    rows = await _fetchall(
        "SELECT s.signal_id, s.direction, s.signal_name, "
        "p.day_offset, p.high_pct, p.low_pct, p.close_pct "
        "FROM cfzy_biz_signal_perf p JOIN cfzy_biz_signals s ON s.id = p.signal_pk "
        "WHERE s.user_id = %s AND s.triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
        (user_id, days_back),
    )
    if not rows:
        return []

    from collections import defaultdict
    meta: dict[str, tuple] = {}
    buckets: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        sid = r["signal_id"]
        meta[sid] = (r["direction"] or "", r["signal_name"] or sid)
        buckets[sid][int(r["day_offset"])].append(
            (float(r["high_pct"] or 0), float(r["low_pct"] or 0), float(r["close_pct"] or 0))
        )

    result = []
    for sid, days in buckets.items():
        direction, name = meta[sid]
        flip = -1.0 if direction in ("sell", "reduce") else 1.0
        curve = []
        for d in sorted(days.keys()):
            vals = days[d]
            m = len(vals)
            rets = [flip * c for (_h, _l, c) in vals]            # 收盘收益(信号视角)
            best = [(h if flip > 0 else -l) for (h, l, _c) in vals]  # 最有利波动
            worst = [(l if flip > 0 else -h) for (h, l, _c) in vals]  # 最不利波动
            curve.append({
                "day": d,
                "samples": m,
                "avg_return": round(sum(rets) / m, 2),
                "avg_best": round(sum(best) / m, 2),
                "avg_worst": round(sum(worst) / m, 2),
                "up_rate": round(sum(1 for x in rets if x > 0) / m * 100, 1),
                "win_rate": round(sum(1 for x in rets if x >= 5) / m * 100, 1),
                "loss_rate": round(sum(1 for x in rets if x <= -3) / m * 100, 1),
            })
        trigger_count = len(days.get(1, [])) or (max((len(v) for v in days.values()), default=0))
        result.append({
            "signal_id": sid, "signal_name": name, "direction": direction,
            "trigger_count": trigger_count, "curve": curve,
        })
    result.sort(key=lambda x: x["signal_id"])
    return result


async def get_signal_outcome_stats(user_id: int = 1, days_back: int = 90) -> dict:
    """按 signal_id 聚合实际 outcome 统计 (近 N 天).

    返回每个 signal_id 的 count / success / fail / neutral / pending / success_rate
    + avg_p1/p3/p5_pct, 给配置页"当前胜率衰减"图用.
    """
    rows = await _fetchall(
        "SELECT signal_id, signal_name, direction, outcome, "
        "outcome_p1_pct, outcome_p3_pct, outcome_p5_pct, triggered_at "
        "FROM cfzy_biz_signals WHERE user_id = %s "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY triggered_at DESC",
        (user_id, days_back),
    )
    if not rows:
        return {}
    grouped: dict[str, dict] = {}
    for r in rows:
        sid = r["signal_id"]
        g = grouped.setdefault(sid, {
            "signal_id": sid,
            "signal_name": r.get("signal_name") or sid,
            "direction": r.get("direction") or "",
            "count": 0,
            "success": 0, "fail": 0, "neutral": 0, "pending": 0,
            "_p1_sum": 0.0, "_p1_n": 0,
            "_p3_sum": 0.0, "_p3_n": 0,
            "_p5_sum": 0.0, "_p5_n": 0,
        })
        g["count"] += 1
        outcome = r.get("outcome")
        if outcome in ("success", "fail", "neutral"):
            g[outcome] += 1
        else:
            g["pending"] += 1
        for k in ("p1", "p3", "p5"):
            v = r.get(f"outcome_{k}_pct")
            if v is not None:
                g[f"_{k}_sum"] += float(v)
                g[f"_{k}_n"] += 1

    out: dict = {}
    for sid, g in grouped.items():
        evaluated = g["success"] + g["fail"] + g["neutral"]
        success_rate = round(g["success"] / evaluated * 100, 1) if evaluated > 0 else 0.0
        item = {
            "signal_id": g["signal_id"],
            "signal_name": g["signal_name"],
            "direction": g["direction"],
            "count": g["count"],
            "evaluated": evaluated,
            "success": g["success"],
            "fail": g["fail"],
            "neutral": g["neutral"],
            "pending": g["pending"],
            "success_rate": success_rate,
        }
        for k in ("p1", "p3", "p5"):
            n = g[f"_{k}_n"]
            item[f"avg_{k}_pct"] = round(g[f"_{k}_sum"] / n, 2) if n > 0 else None
        out[sid] = item
    return out


async def get_buy_signals_on_date(date_str: str, user_id: int = 1) -> list[dict]:
    """某交易日触发的全部 buy 信号(含触发价 price)。给报告「买点盈利跟踪」用。"""
    return await _fetchall(
        "SELECT code, name, signal_id, signal_name, price, triggered_at "
        "FROM cfzy_biz_signals WHERE user_id = %s AND direction = 'buy' "
        "AND trigger_date = %s ORDER BY triggered_at",
        (user_id, date_str),
    )


async def get_buy_signal_p5_returns(user_id: int = 1, days_back: int = 30) -> dict:
    """近 N 天 buy 信号已回填的 5 日收盘收益(outcome_p5_pct), 按 signal_id 分组。
    给"该买点盈利因子/胜率"实时计算用。返回 {signal_id: [p5_pct, ...]}。"""
    from collections import defaultdict
    rows = await _fetchall(
        "SELECT signal_id, outcome_p5_pct FROM cfzy_biz_signals "
        "WHERE user_id = %s AND direction = 'buy' AND outcome_p5_pct IS NOT NULL "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
        (user_id, days_back),
    )
    out: dict[str, list] = defaultdict(list)
    for r in rows:
        out[r["signal_id"]].append(float(r["outcome_p5_pct"]))
    return dict(out)


async def get_buy_signal_span(user_id: int = 1, days_back: int = 30) -> dict:
    """近 N 天已回填 buy 信号的真实触发日期区间, 按 signal_id 分组。
    给战绩小节显示"样本到底覆盖哪几天"用(避免把一簇样本误标成跨半年)。
    返回 {signal_id: (first_date, last_date)}。"""
    rows = await _fetchall(
        "SELECT signal_id, MIN(DATE(triggered_at)) AS first_d, "
        "MAX(DATE(triggered_at)) AS last_d FROM cfzy_biz_signals "
        "WHERE user_id = %s AND direction = 'buy' AND outcome_p5_pct IS NOT NULL "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) GROUP BY signal_id",
        (user_id, days_back),
    )
    return {r["signal_id"]: (r["first_d"], r["last_d"]) for r in rows}


def _empty_side() -> dict:
    return {"count": 0, "evaluated": 0, "success": 0, "fail": 0, "neutral": 0,
            "pending": 0, "success_rate": 0.0, "avg_p1": None, "avg_p3": None, "avg_p5": None}


async def get_outcome_compare(user_id: int = 1, days_back: int = 90) -> dict:
    """买点 vs 卖点(含减仓) 整体胜率并排对比 (近 N 天)。
    口径同 outcome-stats: 成功=p5收盘≥+5%(卖点翻转), 成功率=success/已评估。
    返回 {"buy": {...}, "sell": {...}}。
    """
    rows = await _fetchall(
        "SELECT CASE WHEN direction IN ('sell','reduce') THEN 'sell' ELSE 'buy' END AS side, "
        "COUNT(*) AS cnt, "
        "SUM(outcome = 'success') AS success, SUM(outcome = 'fail') AS fail, "
        "SUM(outcome = 'neutral') AS neutral, SUM(outcome IS NULL) AS pending, "
        "AVG(outcome_p1_pct) AS avg_p1, AVG(outcome_p3_pct) AS avg_p3, AVG(outcome_p5_pct) AS avg_p5 "
        "FROM cfzy_biz_signals WHERE user_id = %s "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) GROUP BY side",
        (user_id, days_back),
    )
    out = {"buy": _empty_side(), "sell": _empty_side()}
    for r in rows:
        side = r["side"]
        success = int(r["success"] or 0)
        fail = int(r["fail"] or 0)
        neutral = int(r["neutral"] or 0)
        evaluated = success + fail + neutral
        out[side] = {
            "count": int(r["cnt"] or 0),
            "evaluated": evaluated,
            "success": success, "fail": fail, "neutral": neutral,
            "pending": int(r["pending"] or 0),
            "success_rate": round(success / evaluated * 100, 1) if evaluated > 0 else 0.0,
            "avg_p1": round(float(r["avg_p1"]), 2) if r["avg_p1"] is not None else None,
            "avg_p3": round(float(r["avg_p3"]), 2) if r["avg_p3"] is not None else None,
            "avg_p5": round(float(r["avg_p5"]), 2) if r["avg_p5"] is not None else None,
        }
    return out


async def get_weekly_outcome_trend(user_id: int = 1, weeks: int = 12) -> list[dict]:
    """买/卖成功率的按周趋势 (近 N 周, ISO 周一为起)。
    返回 [{"week_start": "YYYY-MM-DD", "buy": {evaluated,success,rate}, "sell": {...}}, ...] 周升序。
    """
    rows = await _fetchall(
        "SELECT YEARWEEK(triggered_at, 3) AS yw, "
        "MIN(DATE(DATE_SUB(triggered_at, INTERVAL WEEKDAY(triggered_at) DAY))) AS wk_start, "
        "CASE WHEN direction IN ('sell','reduce') THEN 'sell' ELSE 'buy' END AS side, "
        "SUM(outcome = 'success') AS success, "
        "SUM(outcome IN ('success','fail','neutral')) AS evaluated "
        "FROM cfzy_biz_signals WHERE user_id = %s "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s WEEK) "
        "GROUP BY yw, side ORDER BY yw",
        (user_id, weeks),
    )
    by_week: dict = {}
    for r in rows:
        yw = int(r["yw"])
        wk = by_week.setdefault(yw, {
            "week_start": str(r["wk_start"]),
            "buy": {"evaluated": 0, "success": 0, "rate": None},
            "sell": {"evaluated": 0, "success": 0, "rate": None},
        })
        ev = int(r["evaluated"] or 0)
        su = int(r["success"] or 0)
        wk[r["side"]] = {"evaluated": ev, "success": su,
                         "rate": round(su / ev * 100, 1) if ev > 0 else None}
    return [by_week[k] for k in sorted(by_week.keys())]


async def get_model_weekly_outcome(user_id: int = 1, weeks: int = 8) -> dict:
    """按 (买点模型 × 周) 聚合真实成功率 — 给"当前行情适合哪个模型"面板 (v1.7.x).

    用真实触发的买点信号 + 回填 outcome(1/3/5日收盘). 仅 direction='buy'.
    返回 {
      "weeks": ["YYYY-MM-DD", ...],                         # 周一为周起, 升序
      "models": [{signal_id, signal_name, cells:[{week_start,evaluated,success,rate,avg_p5}],
                  recent_eval, recent_success, recent_rate}],  # recent=最近2周合计(更稳的"当前适合度")
    }
    """
    rows = await _fetchall(
        "SELECT signal_id, MAX(signal_name) AS signal_name, "
        "YEARWEEK(triggered_at, 3) AS yw, "
        "MIN(DATE(DATE_SUB(triggered_at, INTERVAL WEEKDAY(triggered_at) DAY))) AS wk_start, "
        "SUM(outcome = 'success') AS success, "
        "SUM(outcome IN ('success','fail','neutral')) AS evaluated, "
        "AVG(outcome_p5_pct) AS avg_p5 "
        "FROM cfzy_biz_signals WHERE user_id = %s AND direction = 'buy' "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s WEEK) "
        "GROUP BY signal_id, yw ORDER BY yw",
        (user_id, weeks),
    )
    week_of: dict[int, str] = {}
    models: dict[str, dict] = {}
    for r in rows:
        yw = int(r["yw"])
        week_of[yw] = str(r["wk_start"])
        sid = r["signal_id"]
        m = models.setdefault(sid, {"signal_id": sid, "signal_name": r.get("signal_name") or sid, "cells": {}})
        ev = int(r["evaluated"] or 0)
        su = int(r["success"] or 0)
        m["cells"][yw] = {
            "evaluated": ev, "success": su,
            "rate": round(su / ev * 100, 1) if ev > 0 else None,
            "avg_p5": round(float(r["avg_p5"]), 2) if r["avg_p5"] is not None else None,
        }
    weeks_sorted = sorted(week_of.keys())
    recent_yw = set(weeks_sorted[-2:])   # 最近2周
    out_models = []
    for sid, m in models.items():
        cells = []
        r_ev = r_su = 0
        for yw in weeks_sorted:
            c = m["cells"].get(yw, {"evaluated": 0, "success": 0, "rate": None, "avg_p5": None})
            c = {**c, "week_start": week_of[yw]}
            cells.append(c)
            if yw in recent_yw:
                r_ev += c["evaluated"]
                r_su += c["success"]
        out_models.append({
            "signal_id": sid, "signal_name": m["signal_name"], "cells": cells,
            "recent_eval": r_ev, "recent_success": r_su,
            "recent_rate": round(r_su / r_ev * 100, 1) if r_ev > 0 else None,
        })
    # 按近2周成功率降序(无样本排后), 即"当前最适合的模型"在最前
    out_models.sort(key=lambda x: (x["recent_rate"] is None, -(x["recent_rate"] or 0), -x["recent_eval"]))
    return {"weeks": [week_of[yw] for yw in weeks_sorted], "models": out_models}


async def get_signal_matrix(user_id: int = 1, days_back: int = 14) -> dict:
    """按 (日期 × signal_id) 聚合的命中矩阵, 给预警总览页用 (v1.7.x).

    返回:
      {
        "dates": ["2026-05-15", ..., "2026-05-28"],            # 该期间所有出现过命中的交易日
        "rows": [
          {"signal_id": "BUY_WEAK_EXTREME", "signal_name": "弱势极限",
           "signal_group": "entry", "direction": "buy",
           "counts": [12, 8, 0, 15, 9, 11], "total": 55}
        ]
      }
    counts 与 dates 一一对应, 缺数据日补 0.
    """
    rows = await _fetchall(
        "SELECT signal_id, "
        "       MAX(signal_name) AS signal_name, "
        "       MAX(signal_group) AS signal_group, "
        "       MAX(direction) AS direction, "
        "       DATE(triggered_at) AS d, "
        "       COUNT(*) AS cnt "
        "FROM cfzy_biz_signals "
        "WHERE user_id = %s "
        "  AND triggered_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY) "
        "GROUP BY signal_id, DATE(triggered_at) "
        "ORDER BY signal_id, d",
        (user_id, days_back),
    )
    if not rows:
        return {"dates": [], "rows": []}

    dates_set: set[str] = set()
    by_sig: dict[str, dict] = {}
    for r in rows:
        d = str(r["d"])
        dates_set.add(d)
        sid = r["signal_id"]
        g = by_sig.setdefault(sid, {
            "signal_id": sid,
            "signal_name": r.get("signal_name") or sid,
            "signal_group": r.get("signal_group") or "",
            "direction": r.get("direction") or "",
            "by_date": {},
        })
        g["by_date"][d] = int(r["cnt"])
        if r.get("signal_name"):
            g["signal_name"] = r["signal_name"]
        if r.get("signal_group"):
            g["signal_group"] = r["signal_group"]
        if r.get("direction"):
            g["direction"] = r["direction"]

    dates = sorted(dates_set)
    out_rows = []
    for sid, g in by_sig.items():
        counts = [g["by_date"].get(d, 0) for d in dates]
        out_rows.append({
            "signal_id": sid,
            "signal_name": g["signal_name"],
            "signal_group": g["signal_group"],
            "direction": g["direction"],
            "counts": counts,
            "total": sum(counts),
        })
    out_rows.sort(key=lambda x: (x["signal_group"], -x["total"]))
    return {"dates": dates, "rows": out_rows}


async def get_signal_stats(user_id: int = 1, days_back: int = 30) -> dict:
    """按 signal_id 统计近 N 天命中数 / 平均最高涨幅 / 各档胜率 (摸高视角, v1.7.39)."""
    rows = await _fetchall(
        "SELECT code, signal_id, signal_name, direction, price, triggered_at "
        "FROM cfzy_biz_signals WHERE user_id = %s "
        "AND triggered_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY triggered_at DESC",
        (user_id, days_back),
    )
    if not rows:
        return {}

    codes = list({r["code"] for r in rows if r.get("code")})
    code_kline_map: dict[str, list[dict]] = {c: [] for c in codes}
    if codes:
        min_td = min(str(r["triggered_at"])[:10] for r in rows if r.get("triggered_at"))
        placeholders = ",".join(["%s"] * len(codes))
        kls_all = await _fetchall(
            f"SELECT code, trade_date, high FROM cfzy_sys_kline_cache "
            f"WHERE code IN ({placeholders}) AND trade_date > %s "
            f"ORDER BY code, trade_date ASC",
            (*codes, min_td),
        )
        for k in kls_all:
            code_kline_map[k["code"]].append(k)

    grouped: dict[str, dict] = {}
    for row in rows:
        sid = row["signal_id"]
        code = row.get("code")
        entry = float(row.get("price") or 0)
        td_str = str(row.get("triggered_at"))[:10]
        if not code or entry <= 0:
            continue
        kls = code_kline_map.get(code, [])
        future = [k for k in kls if str(k["trade_date"]) > td_str][:20]
        if not future:
            continue
        max_high = max(float(k["high"] or 0) for k in future)
        if max_high <= 0:
            continue
        max_pct = (max_high - entry) / entry * 100

        g = grouped.setdefault(sid, {
            "signal_id": sid, "signal_name": row["signal_name"],
            "direction": row["direction"], "max_pcts": [],
        })
        g["max_pcts"].append(max_pct)

    result = {}
    for sid, g in grouped.items():
        ms = g["max_pcts"]
        n = len(ms)
        if n == 0:
            continue
        result[sid] = {
            "signal_id": sid,
            "signal_name": g["signal_name"],
            "direction": g["direction"],
            "count": n,
            "avg_max_pct": round(sum(ms) / n, 2),
            "win_5pct": round(sum(1 for x in ms if x >= 5) / n * 100, 0),
            "win_10pct": round(sum(1 for x in ms if x >= 10) / n * 100, 0),
            "win_20pct": round(sum(1 for x in ms if x >= 20) / n * 100, 0),
        }
    return result


async def _fetch_perf_map(signal_ids):
    """批量取 perf 行, 返回 {signal_pk: [perf_row,...]}。"""
    if not signal_ids:
        return {}
    ph = ",".join(["%s"] * len(signal_ids))
    rows = await _fetchall(
        f"SELECT signal_pk, day_offset, high_pct, low_pct, close_pct "
        f"FROM cfzy_biz_signal_perf WHERE signal_pk IN ({ph})",
        tuple(signal_ids),
    )
    m = {}
    for r in rows:
        m.setdefault(r["signal_pk"], []).append(r)
    return m


def _exit_cols(plan, trigger_price, max_gain_pct, max_dd_pct):
    """解析计划性出场为展示列 + 触及标注(复用区间最大浮盈/浮亏, 不另查; 不算实现收益)。"""
    ep = parse_exit_plan(plan, trigger_price)
    tp_label = f"+{ep['tp_pct']:g}% {ep['tp_action']}" if ep["tp_pct"] else ""
    sl_label = f"-{ep['sl_pct']:g}%" if ep["sl_pct"] else ""
    tp_hit = bool(ep["tp_pct"] and max_gain_pct is not None and max_gain_pct >= ep["tp_pct"])
    sl_hit = bool(ep["sl_pct"] and max_dd_pct is not None and max_dd_pct <= -ep["sl_pct"])
    return {
        "tp_label": tp_label, "tp_price": ep["tp_price"], "tp_hit": tp_hit,
        "sl_label": sl_label, "sl_price": ep["sl_price"], "sl_hit": sl_hit,
        "other_exit": ep["other_exit"],
    }


async def get_review_signal_list(user_id, start, end, categories):
    """区间复盘清单: 返回 {start,end,latest_kline_date,rows,summary}。
    收益主算 kline_cache, 个股移出池(kline缺/不足)时回退 perf, 再回退 outcome 字段。
    """
    where_cat = build_category_where(categories)
    sigs = await _fetchall(
        "SELECT id, code, name, signal_id, signal_name, direction, price, detail, "
        "triggered_at, trigger_date, outcome, outcome_p1_pct, outcome_p3_pct, outcome_p5_pct "
        "FROM cfzy_biz_signals "
        "WHERE user_id=%s AND trigger_date BETWEEN %s AND %s AND " + where_cat +
        " ORDER BY trigger_date DESC, code, signal_id",
        (user_id, start, end),
    )
    if not sigs:
        return {"start": start, "end": end, "latest_kline_date": None, "rows": [], "summary": []}

    codes = sorted({s["code"] for s in sigs})
    earliest = min(str(s["trigger_date"]) for s in sigs)
    # fetch_kline_cache_for_codes 用的是 trade_date > min, 故传触发日前一天以含触发日当根
    kmin = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
    kline_map = await fetch_kline_cache_for_codes(codes, kmin)
    perf_map = await _fetch_perf_map([s["id"] for s in sigs])

    latest_kline_date = None
    for rows in kline_map.values():
        for r in rows:
            d = str(r["trade_date"])
            if latest_kline_date is None or d > latest_kline_date:
                latest_kline_date = d

    out_rows = []
    for s in sigs:
        kl = kline_map.get(s["code"], [])
        kl_after = [k for k in kl if str(k["trade_date"]) >= str(s["trigger_date"])]
        frozen = False
        if s["price"] and kl_after:
            ret = compute_kline_returns(s["price"], s["trigger_date"], kl)
        else:
            ret = returns_from_perf(perf_map.get(s["id"])) or returns_from_outcome(s)
            if ret is None:
                ret = compute_kline_returns(None, s["trigger_date"], [])  # 全 None
            else:
                frozen = True
        plan = extract_trade_plan(s["detail"])
        out_rows.append({
            "code": s["code"], "name": s["name"],
            "signal_id": s["signal_id"], "signal_name": s["signal_name"],
            "direction": s["direction"],
            "trigger_date": str(s["trigger_date"]),
            "trigger_time": s["triggered_at"].strftime("%H:%M") if s["triggered_at"] else "",
            "trigger_price": s["price"],
            "cur_price": ret["cur_price"], "cur_ret_pct": ret["cur_ret_pct"],
            "max_gain_pct": ret["max_gain_pct"], "max_dd_pct": ret["max_dd_pct"],
            "t1_pct": ret["t1_pct"], "t3_pct": ret["t3_pct"], "t5_pct": ret["t5_pct"],
            "frozen": frozen,
            "outcome": s["outcome"],
            "trade_plan": plan,
            "detail": s["detail"],
            **_exit_cols(plan, s["price"], ret["max_gain_pct"], ret["max_dd_pct"]),
        })

    return {
        "start": start, "end": end, "latest_kline_date": latest_kline_date,
        "rows": out_rows, "summary": summarize_rows(out_rows),
    }
