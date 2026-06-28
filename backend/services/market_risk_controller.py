# -*- coding: utf-8 -*-
"""市场风险两级预警 — 状态机 (v1.7.x 替代原空仓预警).

回测背书 (backend/scripts/bt_market_risk.py + bt_risk_state_machine.py):
  GREEN(正常): 广度≥30% 涨跌比≥30% 炸板率≤60% — 信号胜率52%均值+4.6% PF2.20
  YELLOW(谨慎): 触发轻度预警 — 信号胜率56%均值+4.1% PF2.01(犹豫中上涨, 不减仓仅提示)
  RED(空仓):   5日均收益<-1% 或 新低>15% 或 广度<15% — 信号胜率30%均值-3.6% PF0.47
  状态机: 32.9万条日记录回测, RED覆盖13%交易日, 正确捕获2026-03塌月(n=29均值-9.2%)

指标数据源:
  - 历史(≤昨日): kline_cache 全市场日线 → 涨跌比/均收益/涨停数/新低比
  - 当日: 新浪 Market_Center 快照(收盘后现价=收盘价)
  - 广度MA20: 复用 cfzy_sys_market_breadth (market_breadth_1535 每日盘后产出)

任务:
  market_risk_eod      16:40 cron — 历史指标+当日快照 → 状态机 → 迁移推送 → 落库
  market_risk_intraday 14:40 cron — 只升不降: 同口径估当日指标, 达进入条件提前升级
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx

from backend.models import repository
from backend.models.repo._db import _execute, _fetchall, _fetchone

logger = logging.getLogger(__name__)

_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

# 状态常量
GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"

# 状态机阈值 (回测标定)
YELLOW_ENTER_BREADTH = 30.0       # 广度 < 30%
YELLOW_ENTER_ADVANCE = 30.0       # 涨跌比 < 30%
YELLOW_ENTER_ZHA = 60.0           # 炸板率 > 60%
RED_ENTER_AVG5 = -1.0             # 全市场5日均收益 < -1.0%
RED_ENTER_LOW52 = 15.0            # 52周新低占比 > 15%
RED_ENTER_BREADTH = 15.0          # 广度 < 15%
YELLOW_EXIT_BREADTH = 38.0        # 广度 > 38%
YELLOW_EXIT_ADVANCE = 42.0        # 涨跌比 > 42%
RED_EXIT_BREADTH = 25.0           # 广度 > 25%
RED_EXIT_ADVANCE = 40.0           # 涨跌比 > 40%

_active_cache: tuple[float, str] = (0.0, GREEN)
_ACTIVE_TTL = 120.0


# ── DB helpers ──

async def _upsert_risk(trade_date: str, row: dict) -> None:
    await _execute(
        "INSERT INTO cfzy_biz_market_risk "
        "(trade_date, advance_ratio, breadth_ma20, avg_ret_ma5, low52_ratio, "
        " zha_rate, state, source) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        "advance_ratio=VALUES(advance_ratio), breadth_ma20=VALUES(breadth_ma20), "
        "avg_ret_ma5=VALUES(avg_ret_ma5), low52_ratio=VALUES(low52_ratio), "
        "zha_rate=VALUES(zha_rate), state=VALUES(state), source=VALUES(source)",
        (trade_date, row.get("advance_ratio"), row.get("breadth_ma20"),
         row.get("avg_ret_ma5"), row.get("low52_ratio"), row.get("zha_rate"),
         row["state"], row.get("source", "eod")))


async def _get_prev_state(before_date: str) -> str:
    row = await _fetchone(
        "SELECT state FROM cfzy_biz_market_risk WHERE trade_date < %s "
        "ORDER BY trade_date DESC LIMIT 1", (before_date,))
    return str(row["state"]) if row else GREEN


async def _get_row(trade_date: str) -> dict | None:
    return await _fetchone(
        "SELECT * FROM cfzy_biz_market_risk WHERE trade_date = %s", (trade_date,))


async def _get_recent_breadth(n: int = 6) -> list[float]:
    """取最近N日的广度MA20, 从 market_breadth 表."""
    rows = await _fetchall(
        "SELECT trade_date, ma20_ratio FROM cfzy_sys_market_breadth "
        "ORDER BY trade_date DESC LIMIT %s", (n,))
    return [float(r["ma20_ratio"]) for r in reversed(rows)]


# ── 指标计算 ──

def _limit_of(code: str) -> float:
    return 0.20 if code[:2] in ("30", "68") else 0.10


def _hist_indicators(rows: list[tuple], need_days: int = 6) -> list[dict]:
    """kline_cache (code, date, close, high, low) → 逐交易日指标.

    返回 [{date, advance_ratio, avg_ret, zt_count, zha_rate, low52_ratio}]
    末6日(含残缺尾日剔除).
    """
    by_code: dict[str, list[tuple]] = defaultdict(list)
    for code, d, c, h, l in rows:
        code = str(code)
        if code[:1] in ("8", "4") or code[:2] == "92":
            continue
        try:
            by_code[code].append((str(d)[:10], float(c), float(h), float(l)))
        except (TypeError, ValueError):
            continue

    # 日聚合
    daily = defaultdict(lambda: {
        "adv": 0, "dec": 0, "ret_sum": 0.0, "zt": 0, "zt_touch": 0, "zt_fail": 0,
        "low52_cnt": 0, "n": 0,
    })
    for code, seq in by_code.items():
        seq.sort()
        thr = _limit_of(code) - 0.005
        # 52周高低: 滚动窗口
        for j in range(1, len(seq)):
            d, c, h, l = seq[j]
            c0 = seq[j - 1][1]
            if c0 <= 0:
                continue
            r = c / c0 - 1.0
            dd = daily[d]
            dd["n"] += 1
            dd["ret_sum"] += r
            if r > 0:
                dd["adv"] += 1
            elif r < 0:
                dd["dec"] += 1
            if r >= thr:
                dd["zt"] += 1
            # 炸板
            lp = c0 * (1 + thr)
            if h >= lp * 0.99:
                dd["zt_touch"] += 1
                if c / lp < 0.97:
                    dd["zt_fail"] += 1
            # 52周低点
            start = max(0, j - 52)
            l52 = min(seq[k][3] for k in range(start, j) if seq[k][3] > 0)
            if l52 > 0 and c <= l52 * 1.001:
                dd["low52_cnt"] += 1

    dates = sorted(d for d, v in daily.items() if v["n"] >= 1000)
    # 残缺尾日剔除
    while len(dates) >= 2 and daily[dates[-1]]["n"] < 0.8 * daily[dates[-2]]["n"]:
        dropped = dates.pop()
        logger.info(f"[market_risk] 尾日 {dropped} 覆盖 {daily[dropped]['n']}/{daily[dates[-1]]['n']} 残缺, 剔除")

    out = []
    for d in dates[-need_days:]:
        v = daily[d]
        n = v["n"]
        out.append({
            "date": d,
            "advance_ratio": v["adv"] / max(v["adv"] + v["dec"], 1) * 100,
            "avg_ret": v["ret_sum"] / n * 100,
            "zt_count": v["zt"],
            "zha_rate": v["zt_fail"] / max(v["zt_touch"], 1) * 100,
            "low52_ratio": v["low52_cnt"] / n * 100,
        })
    return out


async def _today_snapshot() -> dict:
    """新浪全市场快照 → {code: 涨跌幅(小数)}."""
    out: dict[str, float] = {}
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_connections=10),
        trust_env=False,
    )
    try:
        page = 1
        while page <= 90:
            params = {"page": page, "num": 80, "sort": "symbol", "asc": 1,
                      "node": "hs_a", "symbol": "", "_s_r_a": "page"}
            try:
                r = await client.get(_LIST_URL, params=params, headers=_HEADERS)
                txt = (r.text or "").strip()
                if not txt or txt == "null":
                    break
                rows = json.loads(txt)
            except Exception:
                break
            if not rows:
                break
            for it in rows:
                sym = it.get("symbol", "")
                name = it.get("name", "")
                if sym.startswith("bj") or not (sym.startswith("sh") or sym.startswith("sz")):
                    continue
                if "ST" in name or "退" in name or name.startswith("*"):
                    continue
                try:
                    trade = float(it.get("trade") or 0)
                    settle = float(it.get("settlement") or 0)
                except (TypeError, ValueError):
                    continue
                if trade <= 0 or settle <= 0:
                    continue
                out[sym[2:]] = trade / settle - 1.0
            page += 1
    finally:
        await client.aclose()
    return out


def _today_from_snapshot(snap: dict[str, float]) -> dict:
    """快照 → 当日指标."""
    n = len(snap)
    if n < 1000:
        return {}
    adv = sum(1 for r in snap.values() if r > 0)
    dec = sum(1 for r in snap.values() if r < 0)
    zt = sum(1 for code, r in snap.items() if r >= _limit_of(code) - 0.005)
    return {
        "advance_ratio": adv / max(adv + dec, 1) * 100,
        "avg_ret": sum(snap.values()) / n * 100,
        "zt_count": zt,
        "n": n,
    }


# ── 状态机 ──

def _run_state_machine(prev_state: str, today: dict, breadth: float | None) -> str:
    """纯函数: 前一状态 + 今日指标 → 新状态."""
    ar = today.get("advance_ratio", 50)
    ar5 = today.get("avg_ret_ma5", 0)
    l52 = today.get("low52_ratio", 0)
    zr = today.get("zha_rate", 0)
    br = breadth if breadth is not None else today.get("breadth_ma20", 30)

    if prev_state == GREEN:
        if br < YELLOW_ENTER_BREADTH or ar < YELLOW_ENTER_ADVANCE or zr > YELLOW_ENTER_ZHA:
            return YELLOW
        return GREEN
    elif prev_state == YELLOW:
        if ar5 < RED_ENTER_AVG5 or l52 > RED_ENTER_LOW52 or br < RED_ENTER_BREADTH:
            return RED
        if br > YELLOW_EXIT_BREADTH and ar > YELLOW_EXIT_ADVANCE and ar5 > 0:
            return GREEN
        return YELLOW
    else:  # RED
        if br > RED_EXIT_BREADTH and ar > RED_EXIT_ADVANCE and ar5 > 0:
            return YELLOW
        return RED


# ── 公共接口 ──

def _invalidate_cache() -> None:
    global _active_cache
    _active_cache = (0.0, GREEN)


async def is_risk_active() -> bool:
    """当前是否处于 RED 风险状态 — 买点推送抑制用, 2 分钟缓存."""
    global _active_cache
    now = time.monotonic()
    if now - _active_cache[0] < _ACTIVE_TTL:
        return _active_cache[1] == RED
    try:
        rows = await _fetchall(
            "SELECT state FROM cfzy_biz_market_risk ORDER BY trade_date DESC LIMIT 1")
        st = str(rows[0]["state"]) if rows else GREEN
    except Exception:
        st = GREEN
    _active_cache = (now, st)
    return st == RED


async def get_risk_state() -> str:
    """获取当前风险状态 (GREEN/YELLOW/RED)."""
    global _active_cache
    now = time.monotonic()
    if now - _active_cache[0] < _ACTIVE_TTL:
        return _active_cache[1]
    try:
        rows = await _fetchall(
            "SELECT state FROM cfzy_biz_market_risk ORDER BY trade_date DESC LIMIT 1")
        st = str(rows[0]["state"]) if rows else GREEN
    except Exception:
        st = GREEN
    _active_cache = (now, st)
    return st


# ── 推送 ──

async def _push(text: str, title: str, template: str) -> None:
    try:
        from backend.services import notifier
        await notifier.send_dual(text, lark_title=title, template=template)
    except Exception as e:
        logger.warning(f"[market_risk] 推送失败({title}): {e}")


# ── 定时入口 ──

async def _gather_metrics() -> tuple[list[dict], dict, float | None] | None:
    """历史指标 + 当日快照 + 广度 → (历史列表, 今日dict, 昨日广度).

    返回 None = 数据不足, 调用方跳过.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")

    # 历史: kline_cache
    from backend.models.database import get_pool
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT code, trade_date, close, high, low FROM cfzy_sys_kline_cache "
                "WHERE trade_date >= %s", (start,))
            rows = list(await cur.fetchall())

    hist = await asyncio.to_thread(_hist_indicators, rows, 6)
    hist = [h for h in hist if h["date"] < today]
    if len(hist) < 5:
        logger.warning(f"[market_risk] 历史指标不足5日({len(hist)}), 跳过")
        return None

    # 广度: 从 market_breadth 表取
    breadth_rows = await _get_recent_breadth(2)
    yest_breadth = breadth_rows[-1] if breadth_rows else None

    # 当日: 新浪快照
    snap = await _today_snapshot()
    if len(snap) < 3000:
        logger.warning(f"[market_risk] 全市场快照仅{len(snap)}只, 跳过")
        return None
    cur = _today_from_snapshot(snap)
    if not cur:
        return None
    cur["date"] = today

    # 今日的衍生指标合并(5日均收益等)
    full_seq = hist + [cur]
    cur["avg_ret_ma5"] = sum(d["avg_ret"] for d in full_seq[-5:]) / 5.0

    # 今日的炸板率/新低比从历史推算(当日快照无这些)
    cur["zha_rate"] = hist[-1].get("zha_rate", 0)  # 近似用昨日
    cur["low52_ratio"] = hist[-1].get("low52_ratio", 0)

    return hist, cur, yest_breadth


async def market_risk_eod():
    """16:40 收盘评估: 指标 → 状态机 → 推送 → 落库."""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return

    got = await _gather_metrics()
    if not got:
        return
    hist, cur, yest_breadth = got
    today = cur["date"]
    prev_state = await _get_prev_state(today)
    existing = await _get_row(today)
    already_state = existing["state"] if existing else None

    new_state = _run_state_machine(prev_state, cur, yest_breadth)

    await _upsert_risk(today, {
        "advance_ratio": cur["advance_ratio"],
        "breadth_ma20": yest_breadth,
        "avg_ret_ma5": cur.get("avg_ret_ma5"),
        "low52_ratio": cur.get("low52_ratio"),
        "zha_rate": cur.get("zha_rate"),
        "state": new_state,
        "source": "eod",
    })
    _invalidate_cache()

    # 状态迁移推送
    if new_state == RED and prev_state != RED and already_state != RED:
        await _push(
            f"[RED] 市场风险·空仓预警\n\n"
            f"全市场5日均收益 {cur['avg_ret_ma5']:+.2f}%, 广度MA20 {yest_breadth:.1f}%, "
            f"新低占比 {cur['low52_ratio']:.1f}%.\n"
            f"回测: RED期内买入信号胜率 30.3%/均值 -3.56%, 强烈建议空仓或停开新仓.\n"
            f"解除条件: 广度>25% 且 涨跌比>40% 且 全市场5日均收益>0%.",
            "[RED] 市场风险·空仓预警", "red")
    elif new_state == YELLOW and prev_state == GREEN:
        await _push(
            f"[YELLOW] 市场风险·谨慎\n\n"
            f"涨跌比 {cur['advance_ratio']:.1f}% 广度MA20 {yest_breadth:.1f}% "
            f"炸板率 {cur['zha_rate']:.1f}% — 触发轻度预警.\n"
            f"回测: YELLOW期信号质量未显著下降(胜率56%/均值+4.1%), 正常交易但注意风控.",
            "[YELLOW] 市场风险·谨慎", "yellow")
    elif new_state == GREEN and prev_state != GREEN:
        await _push(
            f"[GREEN] 市场风险解除\n\n"
            f"广度MA20 {yest_breadth:.1f}% 涨跌比 {cur['advance_ratio']:.1f}% — "
            f"恢复至正常水平, 全仓操作.",
            "[GREEN] 市场风险解除", "green")
    elif new_state == GREEN and already_state == RED:
        await _push(
            f"[GREEN] 盘中预升级撤销\n\n"
            f"收盘复核: 指标未达RED进入条件, 14:40盘中预升级按收盘数据撤销.",
            "[GREEN] 盘中预升级撤销", "green")

    logger.info(f"[market_risk] EOD {today}: ar={cur['advance_ratio']:.1f}% "
                f"br={yest_breadth} avg5={cur['avg_ret_ma5']:+.2f}% "
                f"state {prev_state}->{new_state}")


async def market_risk_intraday():
    """14:40 盘中预升级(只升不降): 同口径估当日指标, 达RED条件提前升级."""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    prev_state = await _get_prev_state(today)
    if prev_state == RED:
        return
    existing = await _get_row(today)
    if existing and existing["state"] == RED:
        return

    got = await _gather_metrics()
    if not got:
        return
    hist, cur, yest_breadth = got
    new_state = _run_state_machine(prev_state, cur, yest_breadth)

    if new_state != RED:
        return  # 盘中只升到RED, GREEN<->YELLOW不管

    await _upsert_risk(today, {
        "advance_ratio": cur["advance_ratio"],
        "breadth_ma20": yest_breadth,
        "avg_ret_ma5": cur.get("avg_ret_ma5"),
        "low52_ratio": cur.get("low52_ratio"),
        "zha_rate": cur.get("zha_rate"),
        "state": RED,
        "source": "intraday",
    })
    _invalidate_cache()
    await _push(
        f"[RED] 市场风险·空仓预警(盘中预升级)\n\n"
        f"全市场5日均收益 {cur['avg_ret_ma5']:+.2f}% 广度MA20 {yest_breadth:.1f}% — "
        f"盘中已达RED进入条件, 提前升级. 16:40收盘复核确认.\n"
        f"建议: 尾盘不新开仓.",
        "[RED] 空仓预警(盘中预升级)", "red")
    logger.warning(f"[market_risk] 盘中预升级 RED: "
                   f"ar5={cur['avg_ret_ma5']:+.2f}% br={yest_breadth}")


# ── 盘中实时检测 (10:00-14:30, 每5分钟) ──

# 实时阈值比EOD更严(盘中噪声大, 宁漏不误)
RT_ADVANCE_RED = 22.0        # 涨跌比 < 22% (EOD=30%)
RT_AVG_RET_RED = -2.0        # 均收益 < -2.0% (EOD=-1.0%)
RT_MIN_STOCKS = 50           # 至少50只有效行情
RT_QUOTE_STALE_SEC = 180     # 行情超过3分钟视为过期
RT_YELLOW_ADVANCE = 28.0     # YELLOW: 涨跌比 < 28%
RT_YELLOW_AVG = -1.0         # YELLOW: 均收益 < -1.0%

_realtime_push_count: dict[str, int] = {}   # date -> 当日推送次数
_REALTIME_MAX_PUSHES = 4                      # 每日最多4条(2轮预警+撤销)


async def market_risk_realtime():
    """盘中实时检测: 用股票池实时行情(非全市场), 每5分钟跑.

    升级: GREEN→YELLOW, YELLOW→RED 均可.
    撤销: 触发条件不再满足时, 回退状态 + 推送撤销.
    同日: 预警最多推1次, 撤销最多推1次 (2条/天上限).
    """
    from backend.core.trading_calendar import is_trading_time as _is_trading_time
    if not _is_trading_time():
        return
    now = datetime.now()
    t = now.strftime("%H:%M")
    if not ("10:00" <= t <= "14:30"):
        return
    today = now.strftime("%Y-%m-%d")

    # 读股票池实时行情
    from backend.models.repo._db import _fetchall
    rows = await _fetchall(
        "SELECT code, pct_change FROM cfzy_biz_stock_pool "
        "WHERE deleted_at IS NULL AND pct_change IS NOT NULL "
        "AND quote_updated_at > NOW() - INTERVAL %s SECOND",
        (RT_QUOTE_STALE_SEC,))
    if len(rows) < RT_MIN_STOCKS:
        return

    n = len(rows)
    pcts = [float(r["pct_change"]) for r in rows]
    avg_ret = sum(pcts) / n
    adv = sum(1 for p in pcts if p > 0)
    dec = sum(1 for p in pcts if p < 0)
    advance_ratio = adv / max(adv + dec, 1) * 100

    # 根据实时指标判定"应然状态"
    if advance_ratio < RT_ADVANCE_RED and avg_ret < RT_AVG_RET_RED:
        should_be = RED
    elif advance_ratio < RT_YELLOW_ADVANCE or avg_ret < RT_YELLOW_AVG:
        should_be = YELLOW
    else:
        should_be = GREEN

    # 当前库里的实时状态(只看 source=realtime 的行, 防与EOD冲突)
    existing = await _get_row(today)
    current_rt = existing["state"] if existing and existing.get("source") == "realtime" else GREEN
    prev_eod = await _get_prev_state(today)

    if should_be == current_rt:
        return  # 无变化

    # ── 升级 ──
    if (should_be == RED and current_rt != RED) or (should_be == YELLOW and current_rt == GREEN):
        if _realtime_push_count.get(today, 0) >= _REALTIME_MAX_PUSHES:
            return  # 今日推送已达上限
        _realtime_push_count[today] = _realtime_push_count.get(today, 0) + 1

        await _upsert_risk(today, {
            "advance_ratio": advance_ratio, "breadth_ma20": None,
            "avg_ret_ma5": avg_ret, "low52_ratio": None, "zha_rate": None,
            "state": should_be, "source": "realtime",
        })
        _invalidate_cache()

        reason = (f"涨跌比 {advance_ratio:.1f}% 均收益 {avg_ret:+.2f}%")
        if should_be == RED:
            await _push(
                f"[RED] 市场风险·实时预警\n\n"
                f"盘中实时: {reason}\n样本 {n} 只自选股 — 已达RED空仓条件.\n"
                f"16:40收盘全市场复核为最终判定.\n建议: 立即停止开新仓.",
                "[RED] 实时预警·空仓", "red")
        else:
            await _push(
                f"[YELLOW] 市场风险·实时提示\n\n"
                f"盘中实时: {reason}\n样本 {n} 只自选股 — 已达YELLOW谨慎条件.\n"
                f"注意风控, 16:40收盘复核.",
                "[YELLOW] 实时提示·谨慎", "yellow")
        logger.info(f"[market_risk] 实时升级 {today} {t}: {current_rt}->{should_be} ({reason})")
        return

    # ── 降级/撤销 ──
    if should_be == GREEN or (should_be == YELLOW and current_rt == RED):
        if _realtime_push_count.get(today, 0) >= _REALTIME_MAX_PUSHES:
            return
        _realtime_push_count[today] = _realtime_push_count.get(today, 0) + 1

        # 回退: 如果实时条件全清, 回退到 EOD 状态; 否则只退一级
        if should_be == GREEN:
            fallback = prev_eod
        else:
            fallback = YELLOW

        await _upsert_risk(today, {
            "advance_ratio": advance_ratio, "breadth_ma20": None,
            "avg_ret_ma5": avg_ret, "low52_ratio": None, "zha_rate": None,
            "state": fallback, "source": "realtime",
        })
        _invalidate_cache()

        reason = f"涨跌比回升至 {advance_ratio:.1f}% 均收益 {avg_ret:+.2f}%"
        if fallback == GREEN:
            await _push(
                f"[GREEN] 市场风险·实时预警解除\n\n"
                f"盘中实时: {reason}\n条件已不满足预警, 恢复至正常状态.\n"
                f"若盘中再度恶化将重新预警. 16:40收盘复核为最终判定.",
                "[GREEN] 实时预警解除", "green")
        else:
            await _push(
                f"[YELLOW] 市场风险·RED降级\n\n"
                f"盘中实时: {reason}\nRED空仓条件解除, 降至YELLOW谨慎.\n"
                f"若盘中再度恶化将重新预警. 16:40收盘复核为最终判定.",
                "[YELLOW] RED降级·谨慎", "yellow")
        logger.info(f"[market_risk] 实时降级 {today} {t}: {current_rt}->{fallback} ({reason})")
