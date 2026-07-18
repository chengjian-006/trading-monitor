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

# (monotonic时刻, 状态, 段起锚点updated_at, 已持续交易日数) — 锚点给横幅的"几点起"用。
# v1.7.678: 锚点取「当前状态连续段的第一天」而非最新行。EOD 每天都会 upsert 重写最新行,
#   updated_at 天天刷新, 哪怕状态一动没动 → 横幅永远显示「昨天16:40起」, 看不出已连续空仓
#   一周多(实测 7/08 起连续 RED, 横幅却写「7月17日 16:40起」)。
_active_cache: tuple[float, str, object, int] = (0.0, GREEN, None, 0)
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
            # 52周(交易日)低点 — v1.7.570: 空 low 序列(全为脏 0)时 min() 会抛 ValueError 炸掉整轮 EOD,
            # 改为收集有效 low 再取 min, 无有效值则跳过本票 low52 计数。
            start = max(0, j - 52)
            lows = [seq[k][3] for k in range(start, j) if seq[k][3] > 0]
            if lows and c <= min(lows) * 1.001:
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
        "adv": adv,      # 涨家数(状态卡多空条用, 状态机不读)
        "dec": dec,      # 跌家数
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
    _active_cache = (0.0, GREEN, None, 0)


def streak_from_rows(rows: list) -> tuple[str, object, int]:
    """风险行(按 trade_date 倒序) → (当前状态, 当前状态连续段第一天的 updated_at, 连续交易日数)。

    纯函数, 路由层也复用(它已有整套行, 不必再查库)。rows 为空 → (GREEN, None, 0)。
    注意按「同一 state 值」断段: RED→YELLOW→RED 会重新计时, 与横幅文案(空仓中/谨慎中)一致。
    """
    if not rows:
        return GREEN, None, 0
    st = str(rows[0]["state"])
    anchor, days = rows[0].get("updated_at"), 0
    for r in rows:
        if str(r["state"]) != st:
            break
        anchor = r.get("updated_at")
        days += 1
    return st, anchor, days


async def _refresh_active_cache() -> None:
    """2分钟缓存刷新: 最新状态 + 当前状态连续段的起始锚点/已持续交易日数。"""
    global _active_cache
    now = time.monotonic()
    if now - _active_cache[0] < _ACTIVE_TTL:
        return
    try:
        rows = await _fetchall(
            "SELECT state, updated_at FROM cfzy_biz_market_risk "
            "ORDER BY trade_date DESC LIMIT 60")
        st, up, days = streak_from_rows(rows)
    except Exception:
        st, up, days = GREEN, None, 0
    _active_cache = (now, st, up, days)


async def is_risk_active() -> bool:
    """当前是否处于 RED 风险状态 — 买点推送抑制用, 2 分钟缓存."""
    await _refresh_active_cache()
    return _active_cache[1] == RED


async def get_risk_state() -> str:
    """获取当前风险状态 (GREEN/YELLOW/RED)."""
    await _refresh_active_cache()
    return _active_cache[1]


async def get_risk_streak_days() -> int:
    """当前状态已连续持续的交易日数(GREEN 也计数, 调用方自行取舍)。"""
    await _refresh_active_cache()
    return _active_cache[3]


async def get_risk_state_info() -> tuple[str, str]:
    """(状态, 时间锚点标签)。锚点=当前状态连续段的第一天(非最新行, 见 _active_cache 注释):
    今日→'13:11', 往日→'7月15日 16:40'; GREEN/无锚点返回 ''。给推送横幅的「几点起」用。"""
    await _refresh_active_cache()
    st, up = _active_cache[1], _active_cache[2]
    label = ""
    if st != GREEN and up is not None:
        try:
            if up.date() == datetime.now().date():
                label = up.strftime("%H:%M")
            else:
                label = f"{up.month}月{up.day}日 {up.strftime('%H:%M')}"
        except Exception:
            label = ""
    return st, label


# ── 状态卡(基线 v1.1): 状态迁移 + 大白话盘面 + [为什么触发] + 👉建议 + 信封字段 ──

_LEVEL = {GREEN: 0, YELLOW: 1, RED: 2}
_BADGE = {GREEN: "🟢 正常", YELLOW: "🟡 谨慎", RED: "🔴 空仓"}
_DANGER = {GREEN: "正常", YELLOW: "谨慎档", RED: "最高危"}   # 档位说明, 让"跳档"一眼看懂危险级别
_TAG = {GREEN: ("正常", "green"), YELLOW: ("谨慎", "orange"), RED: ("空仓", "red")}


async def _push_state_card(title: str, template: str, old_state: str | None, new_state: str,
                           lines: list[str], advice: str, why: str = "",
                           summary: str = "") -> None:
    """市场风险状态卡(基线 v1.1): 状态迁移(带档位说明) + 大白话盘面 + [为什么触发] + 👉建议。

    header 色=风险家族(红空仓/橙谨慎); 信封字段=锁屏摘要(summary, 缺省取状态头) + 状态名彩签。
    走 send_dual_card(飞书原生卡): 时间只在标题栏、正文不写时间; PushPlus 收同款纯文本兜底。"""
    try:
        from backend.services import notifier
        from backend.services.card_kit import advice as _advice_el
        from backend.services.lark_notifier import md_element
        if old_state and old_state != new_state:
            head = (f"{_BADGE.get(old_state, old_state)}　→　"
                    f"{_BADGE.get(new_state, new_state)}（{_DANGER.get(new_state, '')}）")
        else:
            head = f"{_BADGE.get(new_state, new_state)}（{_DANGER.get(new_state, '')}）"
        elements = [md_element(f"**{head}**"), md_element("\n".join(lines))]
        if why:
            elements.append(md_element(f"<font color='grey'>{why}</font>"))
        elements.append(_advice_el(advice))
        text = "\n".join([head, "", *lines] + ([why] if why else []) + ["", f"👉 {advice}"])
        tag_text, tag_color = _TAG.get(new_state, (new_state, "grey"))
        await notifier.send_dual_card(
            text, lark_title=title, elements=elements, template=template,
            summary=summary or f"市场风险 {head}", text_tags=[(tag_text, tag_color)])
    except Exception as e:
        logger.warning(f"[market_risk] 状态卡推送失败({title}): {e}")


async def _push_dismiss(card) -> None:
    """解除卡统一发送口(card_kit.dismiss_card 产物), 失败只记日志不抛。"""
    try:
        from backend.services import notifier
        await notifier.send_card(card)
    except Exception as e:
        logger.warning(f"[market_risk] 解除卡推送失败({getattr(card, 'title', '?')}): {e}")


async def _nongreen_streak(today: str) -> tuple[str, int]:
    """今日之前连续非 GREEN 的段: (发布日标签'M月D日', 生效交易日数)。

    给解除卡的副标题时间线用(基线 v1.1 解除卡标准型)。查库失败/无段返回 ("", 0)。"""
    try:
        rows = await _fetchall(
            "SELECT trade_date, state FROM cfzy_biz_market_risk "
            "WHERE trade_date < %s ORDER BY trade_date DESC LIMIT 60", (today,))
    except Exception:
        return "", 0
    first, n = "", 0
    for r in rows:
        if str(r["state"]) == GREEN:
            break
        first = str(r["trade_date"])[:10]
        n += 1
    if not first:
        return "", 0
    try:
        d = datetime.strptime(first, "%Y-%m-%d")
        return f"{d.month}月{d.day}日", n
    except Exception:
        return first, n


# ── 退潮类维度统一发卡闸(v1.7.556 批次D 六合一) ──
# 退潮(涨停骤降)/溢价转负 不再各自独推, 累积成一张「大盘风控·退潮提示」卡, 卡里带当前风险
# 状态 + 当日已触发的各维度, 按维度集合去重(新增维度才再推)。急跌(plunge)保留即时独推、
# 状态机 RED/YELLOW/GREEN 卡仍各自推(未动回测背书的 proven 引擎)。
_ebb_emit: dict = {}


async def emit_risk_dimension(key: str, text: str, advice_text: str = "") -> None:
    """退潮/溢价等风控维度统一入口: 合并成一张状态化「大盘风控·退潮提示」卡, 当日按维度集合去重。

    基线 v1.1 改造: card_kit.Card(family=risk 橙 / 状态 RED 时红), 结论 heading 行 +
    各维度 ✅式列举(text 为 md, 由调用方给 ✅前缀) + 👉建议(各维度 advice_text 合并)。"""
    today = datetime.now().strftime("%Y-%m-%d")
    st = _ebb_emit.get("st")
    if not st or st["date"] != today:
        st = {"date": today, "dims": {}, "sig": None}
        _ebb_emit["st"] = st
    st["dims"][key] = {"text": text, "advice": advice_text}
    sig = tuple(sorted(st["dims"].keys()))
    if sig == st["sig"]:
        return   # 无新增维度 → 不重复推
    st["sig"] = sig

    try:
        # v1.7.570: 用 get_risk_state(读最新任意日期行)而非 _current_state(只读今日行, 16:40前无行→GREEN)。
        #   原来盘中退潮卡在 EOD 落库前恒显 GREEN, 与买点卡的 RED 警示自相矛盾(基线 RED 日尤甚)。
        state = await get_risk_state()
    except Exception:
        state = GREEN

    from backend.services import card_kit, notifier
    from backend.services.lark_notifier import md_element

    order = {"退潮": 0, "溢价": 1}
    keys = sorted(st["dims"], key=lambda k: order.get(k, 9))
    n = len(keys)
    title = "📛 大盘风控·退潮提示" + (f"（{n}项）" if n > 1 else "")
    tag_text, tag_color = _TAG.get(state, ("正常", "green"))
    badge = _BADGE.get(state, state)

    dims_md = "\n".join(st["dims"][k]["text"] for k in keys)
    advices = []
    for k in keys:
        a = st["dims"][k]["advice"]
        if a and a not in advices:
            advices.append(a)
    advice_line = "；".join(advices) or "整板在抽血，谨慎追高"

    elements = [
        card_kit.heading_md(f"退潮维度 {n} 项触发 · 当前大盘风控 {badge}"),
        md_element(dims_md),
        card_kit.advice(advice_line),
    ]
    fallback = "\n".join([
        f"退潮维度 {n} 项触发 · 当前大盘风控 {badge}", "",
        dims_md, "", f"👉 {advice_line}"])
    card = card_kit.Card(
        title=title, elements=elements, fallback=fallback,
        family="risk_hot" if state == RED else "risk",
        summary=card_kit.summary_text("大盘风控", "·".join(keys) + "提示", f"当前{tag_text}"),
        tags=[(tag_text, tag_color)])
    try:
        await notifier.send_card(card)
    except Exception as e:
        logger.warning(f"[market_risk] 退潮提示卡推送失败: {e}")


async def _current_state() -> str:
    """当前大盘风控状态(读今日 cfzy_biz_market_risk 行, 无则 GREEN)。复用 _active_cache。"""
    today = datetime.now().strftime("%Y-%m-%d")
    row = await _get_row(today)
    return row["state"] if row else GREEN


# ── 定时入口 ──

async def _gather_metrics() -> tuple[list[dict], dict, float | None] | None:
    """历史指标 + 当日快照 + 广度 → (历史列表, 今日dict, 昨日广度).

    返回 None = 数据不足, 调用方跳过.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    # v1.7.570: 拉 100 自然日(≈66-70 交易日) — 原来只拉 35 天(≈23 交易日), 导致 _hist_indicators 里
    #   "52 交易日新低"回看窗被截到约 23 日, "新低占比"系统性偏高、RED 空仓预警偏易误升。
    #   回测 bt_risk_state_machine.py 用的是真 52 交易日滚动低点标定阈值, 拉够数据 = 对齐已验证口径。
    start = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")

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

    # 状态迁移推送(数据区: 涨跌家数多空条, 快照 adv/dec 现成)
    from backend.services import card_kit
    bar = (card_kit.long_short_bar(cur["dec"], cur["adv"])
           if cur.get("adv") is not None else "")
    bar_lines = [bar] if bar else []
    if new_state == RED and prev_state != RED and already_state != RED:
        await _push_state_card(
            "🔴 空仓预警", "red", prev_state, RED,
            [f"全市场走弱: 近5天平均每天 **{cur['avg_ret_ma5']:+.2f}%**, "
             f"守住20日线的票只剩 **{yest_breadth:.0f}%**, 创一年新低的有 {cur['low52_ratio']:.0f}%",
             *bar_lines],
            "**空仓或停开新仓**. 历史上这种时候买入, 10次只赚3次、平均亏3.6%.",
            summary=f"市场风险升至空仓 5日均{cur['avg_ret_ma5']:+.2f}% 广度{yest_breadth:.0f}%")
    elif new_state == YELLOW and prev_state == GREEN:
        await _push_state_card(
            "🟡 转谨慎", "orange", prev_state, YELLOW,
            [f"市场转弱: 只有 **{cur['advance_ratio']:.0f}%** 的票在涨, "
             f"守住20日线的票 {yest_breadth:.0f}%, 涨停里炸板 {cur['zha_rate']:.0f}%",
             *bar_lines],
            "正常交易但注意控制仓位.",
            summary=f"市场风险转谨慎 涨跌比{cur['advance_ratio']:.0f}% 广度{yest_breadth:.0f}%")
    elif new_state == YELLOW and prev_state == RED:
        # v1.7.570: RED→YELLOW 降级原来不推任何通知, 用户一直以为还在空仓预警。补一张降级卡。
        await _push_state_card(
            "🟡 空仓预警降级·转谨慎", "orange", prev_state, YELLOW,
            [f"较前企稳(仍需谨慎): **{cur['advance_ratio']:.0f}%** 的票在涨, "
             f"守住20日线的票回到 {yest_breadth:.0f}%, 已从空仓预警(RED)降到谨慎(YELLOW)",
             *bar_lines],
            "可小仓试探, 但仍需控制仓位、别追高.",
            summary=f"空仓预警降级转谨慎 涨跌比{cur['advance_ratio']:.0f}% 广度{yest_breadth:.0f}%")
    elif new_state == GREEN and prev_state != GREEN:
        # 基线 v1.1 解除卡标准型: 灰 header + 副标题时间线 + 写明解除条件 + 👉建议
        issued, days = await _nongreen_streak(today)
        card = card_kit.dismiss_card(
            "市场风险预警",
            issued_str=issued or "此前", days_active=max(days, 1),
            condition_md=(f"涨跌比 **{cur['advance_ratio']:.0f}%** ≥ {YELLOW_EXIT_ADVANCE:.0f}%、"
                          f"守住20日线 **{yest_breadth:.0f}%** ≥ {YELLOW_EXIT_BREADTH:.0f}%、"
                          f"5日均收益 **{cur['avg_ret_ma5']:+.2f}%** 转正（三条全中）"),
            advice_text="预警解除，恢复正常操作")
        await _push_dismiss(card)
    elif new_state == GREEN and already_state == RED:
        # 盘中预升级撤销: 同为解除卡形态(灰 header 中性收尾)
        card = card_kit.dismiss_card(
            "空仓预警（盘中预升级）",
            issued_str="今日 14:40 盘中", days_active=1,
            condition_md="收盘复核指标未达空仓（RED）进入条件，14:40 盘中预升级按收盘数据撤销",
            advice_text="撤销空仓预警，恢复正常操作")
        await _push_dismiss(card)

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
    from backend.services import card_kit
    bar_lines = ([card_kit.long_short_bar(cur["dec"], cur["adv"])]
                 if cur.get("adv") is not None else [])
    await _push_state_card(
        "🔴 空仓预警(盘中提前)", "red", prev_state, RED,
        [f"盘中已跌到空仓线: 近5天平均每天 **{cur['avg_ret_ma5']:+.2f}%**, "
         f"守住20日线的票只剩 **{yest_breadth:.0f}%**", *bar_lines],
        "**尾盘不新开仓**. 收盘后(16:40)最终确认.",
        summary=f"空仓预警盘中提前 5日均{cur['avg_ret_ma5']:+.2f}% 广度{yest_breadth:.0f}%")
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

# 降级缓冲带(退出阈值明显高于进入, 防贴着一条线来回穿自己打脸) + 冷静期
RT_EXIT_RED_ADVANCE = 35.0      # 脱离空仓(RED): 涨跌比需回到 ≥35%(远高于进入的22%)
RT_EXIT_RED_AVG = -1.2          #                且 平均 ≥ -1.2%
RT_EXIT_YELLOW_ADVANCE = 45.0   # 解除谨慎→正常(GREEN): 涨跌比 ≥45%
RT_EXIT_YELLOW_AVG = -0.3       #                       且 平均 ≥ -0.3%
RT_DOWNGRADE_COOLDOWN_MIN = 30  # 任何降级距上次变档至少30分钟(10分钟内不许反向打脸)

_realtime_push_count: dict[str, int] = {}   # date -> 当日推送次数
_REALTIME_MAX_PUSHES = 4                      # 每日最多4条(2轮预警+撤销)
_realtime_last_change_at: dict[str, datetime] = {}   # date -> 最近一次状态变档时刻(冷静期用)


def _exit_target(current_rt: str, advance_ratio: float, avg_ret: float) -> str:
    """带缓冲带的降级目标: 只有明显转好(过退出线)才降, 且一次最多降到条件允许的最低档。

    退出线远高于进入线 → 状态在缓冲带内保持不动, 不再贴着单条阈值来回抖。"""
    if current_rt == RED:
        if advance_ratio >= RT_EXIT_RED_ADVANCE and avg_ret >= RT_EXIT_RED_AVG:
            if advance_ratio >= RT_EXIT_YELLOW_ADVANCE and avg_ret >= RT_EXIT_YELLOW_AVG:
                return GREEN
            return YELLOW
        return RED
    if current_rt == YELLOW:
        if advance_ratio >= RT_EXIT_YELLOW_ADVANCE and avg_ret >= RT_EXIT_YELLOW_AVG:
            return GREEN
        return YELLOW
    return current_rt


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

    # 应然状态(进入口径, 仅用于升级判定)
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
    level_cur = _LEVEL.get(current_rt, 0)

    # 大白话盘面行(自选池实时) + 涨跌家数多空条(数据现成)
    from backend.services import card_kit
    rt_line = f"自选池{n}只，只 **{advance_ratio:.0f}%** 在涨、平均 **{avg_ret:+.2f}%**"
    rt_bar = card_kit.long_short_bar(dec, adv)

    # ── 升级(转差): 危险要及时报, 不设冷静期 ──
    if _LEVEL.get(should_be, 0) > level_cur:
        if _realtime_push_count.get(today, 0) >= _REALTIME_MAX_PUSHES:
            return  # 今日推送已达上限
        _realtime_push_count[today] = _realtime_push_count.get(today, 0) + 1
        _realtime_last_change_at[today] = now

        await _upsert_risk(today, {
            "advance_ratio": advance_ratio, "breadth_ma20": None,
            "avg_ret_ma5": avg_ret, "low52_ratio": None, "zha_rate": None,
            "state": should_be, "source": "realtime",
        })
        _invalidate_cache()

        if should_be == RED:
            await _push_state_card(
                "🔴 市场风险 · 升到「空仓」档", "red", current_rt, RED,
                [f"盘面大跌：{rt_line}", rt_bar],
                "立即停开新仓、别抄底，今天先保命。（16:40收盘复核，才定这档是否延续到明天）",
                why=f"触发线：<{RT_ADVANCE_RED:.0f}%在涨 且 平均跌超{-RT_AVG_RET_RED:.0f}% = 空仓",
                summary=f"市场风险升到空仓档 {advance_ratio:.0f}%在涨 平均{avg_ret:+.2f}%")
        else:
            await _push_state_card(
                "🟡 市场风险 · 升到「谨慎」档", "orange", current_rt, YELLOW,
                [f"盘面转弱：{rt_line}", rt_bar],
                "注意控制仓位、别追高。（16:40收盘再定档）",
                why=f"触发线：<{RT_YELLOW_ADVANCE:.0f}%在涨 或 平均跌超{-RT_YELLOW_AVG:.0f}% = 谨慎",
                summary=f"市场风险升到谨慎档 {advance_ratio:.0f}%在涨 平均{avg_ret:+.2f}%")
        logger.info(f"[market_risk] 实时升级 {today} {t}: {current_rt}->{should_be} "
                    f"(涨跌比 {advance_ratio:.1f}% 均收益 {avg_ret:+.2f}%)")
        return

    # ── 降级(转好): 带缓冲带 + 冷静期, 防贴线来回打脸 ──
    target = _exit_target(current_rt, advance_ratio, avg_ret)
    # 盘中不擅自解除到EOD基线以下(基线去留由16:40收盘复核定夺)
    if _LEVEL.get(prev_eod, 0) > _LEVEL.get(target, 0):
        target = prev_eod
    if _LEVEL.get(target, 0) >= level_cur:
        return  # 未过退出缓冲线, 维持当前档, 不写不推(不打脸)
    # 冷静期: 距上次变档不足N分钟, 先按兵不动
    last = _realtime_last_change_at.get(today)
    if last and (now - last).total_seconds() < RT_DOWNGRADE_COOLDOWN_MIN * 60:
        return
    if _realtime_push_count.get(today, 0) >= _REALTIME_MAX_PUSHES:
        return
    _realtime_push_count[today] = _realtime_push_count.get(today, 0) + 1
    _realtime_last_change_at[today] = now

    await _upsert_risk(today, {
        "advance_ratio": advance_ratio, "breadth_ma20": None,
        "avg_ret_ma5": avg_ret, "low52_ratio": None, "zha_rate": None,
        "state": target, "source": "realtime",
    })
    _invalidate_cache()

    if target == GREEN:
        # 基线 v1.1 解除卡: 灰 header + 副标题时间线 + 写明解除条件(过缓冲带) + 👉建议。
        # 盘中降到 GREEN 的前提是 EOD 基线本就 GREEN → 预警必是今日盘中发布, 时间线取上次变档时刻。
        issued = f"今日 {last.strftime('%H:%M')}" if last else "今日盘中"
        card = card_kit.dismiss_card(
            "市场风险预警（盘中）",
            issued_str=issued, days_active=1,
            condition_md=(f"自选池涨跌比回到 **{advance_ratio:.0f}%** ≥ "
                          f"{RT_EXIT_YELLOW_ADVANCE:.0f}% 且 平均 **{avg_ret:+.2f}%** ≥ "
                          f"{RT_EXIT_YELLOW_AVG}%（过缓冲带，防贴线反复）"),
            period_md=f"盘面回稳：自选池{n}只 {advance_ratio:.0f}%在涨、平均{avg_ret:+.2f}%",
            advice_text="恢复正常操作，16:40收盘最终定档")
        await _push_dismiss(card)
    else:
        await _push_state_card(
            "🟡 市场风险 · 降到「谨慎」档", "orange", current_rt, YELLOW,
            [f"跌势明显缓和：{rt_line}\n——是没那么急了，不是转多。", rt_bar],
            "空仓警报解除，可小仓试错、别重仓。（16:40收盘最终定档）",
            summary=f"空仓降到谨慎 {advance_ratio:.0f}%在涨 平均{avg_ret:+.2f}%")
    logger.info(f"[market_risk] 实时降级 {today} {t}: {current_rt}->{target} "
                f"(涨跌比回升至 {advance_ratio:.1f}% 均收益 {avg_ret:+.2f}%)")
