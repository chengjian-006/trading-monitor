# -*- coding: utf-8 -*-
"""市场风险两级预警 — 状态机 (v1.7.x 替代原空仓预警).

⚠️ 本模块只做「标注」不做「拦截」: RED 期间买点推送照常发出, 仅由 risk_buy_note()
   在正文加一行警示。历史上曾有 is_risk_active() 看似是抑制开关, 但它从无调用方,
   已于 v1.7.686 删除以免再被误读为安全闸门。要真拦截需另行显式实现。

回测背书 (v1.7.686 重做, backend/scripts/bt_risk_baseline_redo.py):
  样本: 5 个右侧模型 8746 条信号; 零重叠独立样本 OOS 2021-01~2025-05(1054 交易日)
  GREEN(正常): 广度≥30% 涨跌比≥30% 炸板率≤60% — 胜率40.6% 均值-0.49% PF0.89 (覆盖38.8%)
  YELLOW(谨慎): 触发轻度预警 —                    胜率38.6% 均值-1.79% PF0.67 (覆盖39.6%)
  RED(空仓):   5日均收益<-1% 或 新低>15% 或 广度<15% — 胜率36.1% 均值-2.27% PF0.62 (覆盖21.6%)
  → 三档单调递减, 状态机有效; 但**区分力只在坏市场体现**: 同一套规则在 IS 期
    (2025-06~2026-05 普涨市)三档胜率 48.0/49.3/48.3 几乎无差别。

  旧 docstring 曾写 GREEN 52%/+4.6%/PF2.20、RED 30%/-3.6%/PF0.47、RED覆盖13%,
  来自 bt_risk_state_machine.py —— 该脚本状态机用**当天**广度判当天状态(生产用昨日),
  属前视偏差, 且标定期恰为普涨市。复核实测对不上(尤其 GREEN 实为负期望), 已弃用。

指标数据源:
  - 历史(≤昨日): kline_cache 全市场日线 → 涨跌比/均收益/涨停数/新低比
  - 当日: 新浪 Market_Center 快照(收盘后现价=收盘价)
  - 广度MA20: 复用 cfzy_sys_market_breadth (market_breadth_1535 每日盘后产出)

任务 (Deploy 2B, v1.7.752 起本模块 = 大盘唯一预警机制, 三档统一命名 正常/谨慎/危险):
  market_risk_eod      16:40 cron — 历史指标+当日快照 → 状态机 → 迁移推送 → 落库(最终定档)
  market_risk_intraday 14:40 cron — 只升不降: 同口径估当日指标, 达进入条件提前升级
  market_risk_watch    盘中每5分钟 — 全市场口径(market_overview 快照)实时监测,
                       升档即时预警 + 退出机制(过缓冲带才降档/解除, 冷静期防打脸)
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


# ── 0-100 风险分 (v1.7.740, Deploy 2A: 展示用) ──
# 数值越高越危险。**档位仍由 OOS 背书的状态机(_run_state_machine)定夺** —— 本分数不参与
# 档位决策、不改档位边界, 故不触发回测复验(用户 0721 拍板: 分数只做展示)。做法: 每档占一段
# 分数带, 5 个已 OOS 标定的全市场指标折成 0..1「风险压力」, 在本档带内定位 → 数字连续可读且
# 永不与三档戳(正常/谨慎/危险)矛盾。
_SCORE_BAND = {GREEN: (0, 33), YELLOW: (34, 66), RED: (67, 100)}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _risk_pressure(ind: dict) -> float:
    """5 个 OOS 指标 → 0..1 风险压力(越高越危险)。逐维用各自的进入/退出阈值线性归一,
    缺失的维度直接跳过(realtime 行只有涨跌比/均收益), 全缺则中性 0.5。"""
    parts: list[float] = []
    br = ind.get("breadth_ma20")
    if br is not None:
        parts.append(_clamp01((YELLOW_EXIT_BREADTH - float(br)) / (YELLOW_EXIT_BREADTH - RED_ENTER_BREADTH)))
    ar = ind.get("advance_ratio")
    if ar is not None:
        parts.append(_clamp01((YELLOW_EXIT_ADVANCE - float(ar)) / (YELLOW_EXIT_ADVANCE - RED_ENTER_BREADTH)))
    a5 = ind.get("avg_ret_ma5")
    if a5 is not None:
        parts.append(_clamp01((0.0 - float(a5)) / (0.0 - RED_ENTER_AVG5 * 3)))  # 0% → -3% 铺满
    l52 = ind.get("low52_ratio")
    if l52 is not None:
        parts.append(_clamp01((float(l52) - 5.0) / (RED_ENTER_LOW52 + 5.0 - 5.0)))
    zr = ind.get("zha_rate")
    if zr is not None:
        parts.append(_clamp01((float(zr) - YELLOW_ENTER_ZHA + 20.0) / 30.0))  # 40% → 70% 铺满
    return sum(parts) / len(parts) if parts else 0.5


def risk_score_of(state: str, ind: dict) -> int:
    """0-100 风险分(展示): 状态机档位定分数带, 5 指标压力在带内定位。越高越危险。"""
    lo, hi = _SCORE_BAND.get(state, _SCORE_BAND[YELLOW])
    return int(round(lo + _risk_pressure(ind) * (hi - lo)))


# 三档展示名(Deploy 2B retier: RED 档名由「空仓」改「危险」—— 档名说危险程度,
# 「空仓」是该档的操作建议, 建议语里保留)。
_TIER_LABEL = {GREEN: "正常", YELLOW: "谨慎", RED: "危险"}


def tier_label_of(state: str) -> str:
    return _TIER_LABEL.get(state, "正常")


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


# 买点推送的风险档警示语。数字全部来自 bt_risk_baseline_redo.py 的 OOS 实测
# (2021-01~2025-05, 1054 交易日, 8746 条信号), 不再用旧脚本那串带前视偏差的数字。
# 按模型分流的依据: RED 档伤害极度集中在平台突破(OOS 均-5.06% PF0.31, 全模型最差,
# 且 IS 期同样垫底 = 双段同向); 回踩MA10/MA60 在 RED 档两段都接近打平(PF≈1.0)。
# 其余模型 IS/OOS 不同向, 不单独分流, 走通用文案。
_RED_FRAGILE = {"BUY_PLATFORM_BREAKOUT"}
_RED_NEUTRAL = {"BUY_RALLY_MA10", "BUY_RALLY_MA60"}


# 各档警示语的登记表 key → 兜底文案。数字**不写死在这里**: 运行时从
# cfzy_sys_backtest_claims 取(见 backtest_claims 模块 docstring —— 硬编码结论会过期
# 且没人发现, 推送里那句"胜率30%均值-3.6%"就这么用了很久)。兜底只在登记表缺条目或
# 读库失败时用, 绝不让推送因此变哑。
_NOTE_KEYS = {
    ("RED", "fragile"): ("risk_note_red_fragile",
                         "🔴 大盘危险档 · 平台突破在此档最脆，样本内外同向 —— 强烈建议不做"),
    ("RED", "neutral"): ("risk_note_red_neutral",
                         "🔴 大盘危险档 · 大盘整体走弱，但该模型在此档历史上接近打平，若做务必轻仓"),
    ("RED", "generic"): ("risk_note_red", "🔴 大盘危险档 · 明显劣于正常档，建议停开新仓"),
    ("YELLOW", "generic"): ("risk_note_yellow", "⚡ 大盘谨慎档 · 弱于正常档，控制仓位、别追高"),
}


def _note_slot(state: str, signal_id: str) -> tuple | None:
    if state == RED:
        if signal_id in _RED_FRAGILE:
            return _NOTE_KEYS[("RED", "fragile")]
        if signal_id in _RED_NEUTRAL:
            return _NOTE_KEYS[("RED", "neutral")]
        return _NOTE_KEYS[("RED", "generic")]
    if state == YELLOW:
        return _NOTE_KEYS[("YELLOW", "generic")]
    return None


async def risk_buy_note_async(state: str, signal_id: str = "") -> str:
    """买点推送的风险档警示行(数字走登记表)。GREEN/未知档返回 ''。"""
    slot = _note_slot(state, signal_id)
    if not slot:
        return ""
    key, fallback = slot
    try:
        from backend.services import backtest_claims
        return await backtest_claims.text_of(key, fallback)
    except Exception:
        return fallback


def risk_buy_note(state: str, signal_id: str = "") -> str:
    """同步版(兜底文案, 不含具体数字)。新调用一律用 risk_buy_note_async。"""
    slot = _note_slot(state, signal_id)
    return slot[1] if slot else ""


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
_BADGE = {GREEN: "🟢 正常", YELLOW: "🟡 谨慎", RED: "🔴 危险"}
_DANGER = {GREEN: "正常", YELLOW: "谨慎档", RED: "最高危"}   # 档位说明, 让"跳档"一眼看懂危险级别
_TAG = {GREEN: ("正常", "green"), YELLOW: ("谨慎", "orange"), RED: ("危险", "red")}


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


# (v1.7.752 Deploy 2B: 退潮维度统一发卡闸 emit_risk_dimension 已删 —— 唯一调用方
#  market_ebb_detector 于 v1.7.737 退役, 大盘预警统一收口到本模块三档状态机。)


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
            "🔴 市场风险 · 升到「危险」档", "red", prev_state, RED,
            [f"全市场走弱: 近5天平均每天 **{cur['avg_ret_ma5']:+.2f}%**, "
             f"守住20日线的票只剩 **{yest_breadth:.0f}%**, 创一年新低的有 {cur['low52_ratio']:.0f}%",
             *bar_lines],
            "**空仓或停开新仓**. 独立样本实测此档买入, 10次只赚3~4次、平均亏2.3%.",
            summary=f"市场风险升到危险档 5日均{cur['avg_ret_ma5']:+.2f}% 广度{yest_breadth:.0f}%")
    elif new_state == YELLOW and prev_state == GREEN:
        await _push_state_card(
            "🟡 市场风险 · 转「谨慎」档", "orange", prev_state, YELLOW,
            [f"市场转弱: 只有 **{cur['advance_ratio']:.0f}%** 的票在涨, "
             f"守住20日线的票 {yest_breadth:.0f}%, 涨停里炸板 {cur['zha_rate']:.0f}%",
             *bar_lines],
            "正常交易但注意控制仓位.",
            summary=f"市场风险转谨慎 涨跌比{cur['advance_ratio']:.0f}% 广度{yest_breadth:.0f}%")
    elif new_state == YELLOW and prev_state == RED:
        # v1.7.570: RED→YELLOW 降级原来不推任何通知, 用户一直以为还在危险档。补一张降级卡。
        await _push_state_card(
            "🟡 市场风险 · 危险降到「谨慎」档", "orange", prev_state, YELLOW,
            [f"较前企稳(仍需谨慎): **{cur['advance_ratio']:.0f}%** 的票在涨, "
             f"守住20日线的票回到 {yest_breadth:.0f}%, 已从危险(RED)降到谨慎(YELLOW)",
             *bar_lines],
            "可小仓试探, 但仍需控制仓位、别追高.",
            summary=f"危险档降级转谨慎 涨跌比{cur['advance_ratio']:.0f}% 广度{yest_breadth:.0f}%")
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
            "危险预警（盘中预升级）",
            issued_str="今日 14:40 盘中", days_active=1,
            condition_md="收盘复核指标未达危险（RED）进入条件，14:40 盘中预升级按收盘数据撤销",
            advice_text="撤销危险预警，恢复正常操作")
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
        "🔴 市场风险 · 盘中提前升「危险」档", "red", prev_state, RED,
        [f"盘中已跌到危险线: 近5天平均每天 **{cur['avg_ret_ma5']:+.2f}%**, "
         f"守住20日线的票只剩 **{yest_breadth:.0f}%**", *bar_lines],
        "**尾盘不新开仓**. 收盘后(16:40)最终确认.",
        summary=f"危险预警盘中提前 5日均{cur['avg_ret_ma5']:+.2f}% 广度{yest_breadth:.0f}%")
    logger.warning(f"[market_risk] 盘中预升级 RED: "
                   f"ar5={cur['avg_ret_ma5']:+.2f}% br={yest_breadth}")


# ── 盘中实时监测 market_risk_watch (Deploy 2B, v1.7.752: 全市场口径, 每5分钟) ──
# 数据源 = cfzy_sys_market_overview 快照(后台任务已定时刷新, 零新增外部调用):
#   market_stats 全市场涨跌家数(新浪全市场扫描, 5min 缓存) + a_indices 三大指数实时涨跌幅。
# 不再用自选池 —— v1.7.737 退役的 market_risk_realtime 拿自选池代表大盘, 名实不符。
# 阈值为启发式(宁漏不误), 档位最终由 16:40 EOD 状态机(OOS 背书)定夺; 盘中层的价值是
# 「升档即时预警」+「退出机制: 明显转好过缓冲带才降档/解除, 冷静期防打脸」。
WATCH_RED_ADVANCE = 22.0      # 危险: 全市场涨跌比 < 22% 且
WATCH_RED_IDX = -1.5          #       三大指数平均 < -1.5%
WATCH_YELLOW_ADVANCE = 28.0   # 谨慎: 涨跌比 < 28% 或
WATCH_YELLOW_IDX = -1.0       #       指数平均 < -1.0%
# 退出缓冲带(退出线远高于进入线, 防贴着一条线来回穿自己打脸) + 冷静期
WATCH_EXIT_RED_ADVANCE = 35.0     # 脱离危险(RED): 涨跌比需回到 ≥35%(远高于进入的22%)
WATCH_EXIT_RED_IDX = -1.0         #                且 指数平均 ≥ -1.0%
WATCH_EXIT_YELLOW_ADVANCE = 45.0  # 解除谨慎→正常(GREEN): 涨跌比 ≥45%
WATCH_EXIT_YELLOW_IDX = -0.3      #                       且 指数平均 ≥ -0.3%
WATCH_COOLDOWN_MIN = 30       # 任何降级距上次变档至少30分钟(不许反向打脸)
WATCH_MAX_PUSHES = 4          # 每日最多4条(2轮预警+解除)
WATCH_MIN_STOCKS = 3000       # 全市场快照至少3000只有效涨跌家数才可信
WATCH_STALE_SEC = 600         # 快照超过10分钟视为过期不用

_watch_push_count: dict[str, int] = {}            # date -> 当日推送次数
_watch_last_change_at: dict[str, datetime] = {}   # date -> 最近一次状态变档时刻(冷静期用)

_WATCH_INDEX_NAMES = ("上证指数", "深证成指", "创业板指")


def _watch_enter_state(advance_ratio: float, idx_avg: float) -> str:
    """进入口径(仅用于升级判定): 全市场涨跌比% + 三大指数平均涨跌% → 应然状态。纯函数。"""
    if advance_ratio < WATCH_RED_ADVANCE and idx_avg < WATCH_RED_IDX:
        return RED
    if advance_ratio < WATCH_YELLOW_ADVANCE or idx_avg < WATCH_YELLOW_IDX:
        return YELLOW
    return GREEN


def _watch_exit_target(current: str, advance_ratio: float, idx_avg: float) -> str:
    """退出机制(带缓冲带的降级目标): 只有明显转好(过退出线)才降档, 且一次最多降到条件允许的
    最低档。退出线远高于进入线 → 状态在缓冲带内保持不动, 不再贴着单条阈值来回抖。纯函数。"""
    if current == RED:
        if advance_ratio >= WATCH_EXIT_RED_ADVANCE and idx_avg >= WATCH_EXIT_RED_IDX:
            if advance_ratio >= WATCH_EXIT_YELLOW_ADVANCE and idx_avg >= WATCH_EXIT_YELLOW_IDX:
                return GREEN
            return YELLOW
        return RED
    if current == YELLOW:
        if advance_ratio >= WATCH_EXIT_YELLOW_ADVANCE and idx_avg >= WATCH_EXIT_YELLOW_IDX:
            return GREEN
        return YELLOW
    return current


async def _watch_metrics() -> tuple[float, float, int, int] | None:
    """market_overview 快照 → (全市场涨跌比%, 三大指数平均涨跌%, 涨家数, 跌家数)。

    快照缺失/样本不足/过期 → None(本轮跳过, 不臆测)。"""
    try:
        overview = await repository.get_market_overview()
    except Exception as e:
        logger.warning(f"[market_risk] watch 读 market_overview 失败: {e}")
        return None
    if not overview:
        return None
    snap_at = overview.get("snapshot_at") or overview.get("updated_at")
    if snap_at is not None:
        try:
            if isinstance(snap_at, str):
                snap_at = datetime.strptime(snap_at[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - snap_at).total_seconds() > WATCH_STALE_SEC:
                return None
        except Exception:
            pass
    stats = overview.get("market_stats") or {}
    up = int(stats.get("up_count") or 0)
    down = int(stats.get("down_count") or 0)
    if up + down < WATCH_MIN_STOCKS:
        return None
    pcts = []
    for idx in overview.get("a_indices") or []:
        if idx.get("name") in _WATCH_INDEX_NAMES and idx.get("pct_change") is not None:
            try:
                pcts.append(float(idx["pct_change"]))
            except (TypeError, ValueError):
                continue
    if not pcts:
        return None
    advance_ratio = up / max(up + down, 1) * 100
    idx_avg = sum(pcts) / len(pcts)
    return advance_ratio, idx_avg, up, down


async def market_risk_watch():
    """盘中实时监测(全市场口径, 10:00-14:30 每5分钟):

    升档: GREEN→YELLOW / YELLOW→RED 即时预警(不设冷静期, 危险要及时报)。
    退出: 明显转好过退出缓冲带才降档/解除, 且有30分钟冷静期; 盘中不降到EOD基线以下。
    """
    from backend.core.trading_calendar import is_trading_time as _is_trading_time
    if not _is_trading_time():
        return
    now = datetime.now()
    t = now.strftime("%H:%M")
    if not ("10:00" <= t <= "14:30"):
        return
    today = now.strftime("%Y-%m-%d")

    got = await _watch_metrics()
    if not got:
        return
    advance_ratio, idx_avg, up, down = got
    should_be = _watch_enter_state(advance_ratio, idx_avg)

    # 当前库里的盘中状态(只看 source=realtime 的行, 防与EOD冲突)
    existing = await _get_row(today)
    current_rt = existing["state"] if existing and existing.get("source") == "realtime" else GREEN
    prev_eod = await _get_prev_state(today)
    level_cur = _LEVEL.get(current_rt, 0)

    from backend.services import card_kit
    rt_line = (f"全市场{up + down}只，只 **{advance_ratio:.0f}%** 在涨、"
               f"三大指数平均 **{idx_avg:+.2f}%**")
    rt_bar = card_kit.long_short_bar(down, up)

    # ── 升级(转差): 危险要及时报, 不设冷静期 ──
    if _LEVEL.get(should_be, 0) > level_cur:
        if _watch_push_count.get(today, 0) >= WATCH_MAX_PUSHES:
            return  # 今日推送已达上限
        _watch_push_count[today] = _watch_push_count.get(today, 0) + 1
        _watch_last_change_at[today] = now

        await _upsert_risk(today, {
            "advance_ratio": advance_ratio, "breadth_ma20": None,
            "avg_ret_ma5": idx_avg, "low52_ratio": None, "zha_rate": None,
            "state": should_be, "source": "realtime",
        })
        _invalidate_cache()

        if should_be == RED:
            await _push_state_card(
                "🔴 市场风险 · 升到「危险」档", "red", current_rt, RED,
                [f"盘面大跌：{rt_line}", rt_bar],
                "立即停开新仓、别抄底，今天先保命。（16:40收盘复核，才定这档是否延续到明天）",
                why=(f"触发线：<{WATCH_RED_ADVANCE:.0f}%在涨 且 "
                     f"三大指数平均跌超{-WATCH_RED_IDX:.1f}% = 危险"),
                summary=f"市场风险升到危险档 {advance_ratio:.0f}%在涨 指数均{idx_avg:+.2f}%")
        else:
            await _push_state_card(
                "🟡 市场风险 · 升到「谨慎」档", "orange", current_rt, YELLOW,
                [f"盘面转弱：{rt_line}", rt_bar],
                "注意控制仓位、别追高。（16:40收盘再定档）",
                why=(f"触发线：<{WATCH_YELLOW_ADVANCE:.0f}%在涨 或 "
                     f"三大指数平均跌超{-WATCH_YELLOW_IDX:.1f}% = 谨慎"),
                summary=f"市场风险升到谨慎档 {advance_ratio:.0f}%在涨 指数均{idx_avg:+.2f}%")
        logger.info(f"[market_risk] watch升级 {today} {t}: {current_rt}->{should_be} "
                    f"(涨跌比 {advance_ratio:.1f}% 指数均 {idx_avg:+.2f}%)")
        return

    # ── 退出(转好): 带缓冲带 + 冷静期, 防贴线来回打脸 ──
    target = _watch_exit_target(current_rt, advance_ratio, idx_avg)
    # 盘中不擅自解除到EOD基线以下(基线去留由16:40收盘复核定夺)
    if _LEVEL.get(prev_eod, 0) > _LEVEL.get(target, 0):
        target = prev_eod
    if _LEVEL.get(target, 0) >= level_cur:
        return  # 未过退出缓冲线, 维持当前档, 不写不推(不打脸)
    last = _watch_last_change_at.get(today)
    if last and (now - last).total_seconds() < WATCH_COOLDOWN_MIN * 60:
        return
    if _watch_push_count.get(today, 0) >= WATCH_MAX_PUSHES:
        return
    _watch_push_count[today] = _watch_push_count.get(today, 0) + 1
    _watch_last_change_at[today] = now

    await _upsert_risk(today, {
        "advance_ratio": advance_ratio, "breadth_ma20": None,
        "avg_ret_ma5": idx_avg, "low52_ratio": None, "zha_rate": None,
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
            condition_md=(f"全市场涨跌比回到 **{advance_ratio:.0f}%** ≥ "
                          f"{WATCH_EXIT_YELLOW_ADVANCE:.0f}% 且 三大指数平均 **{idx_avg:+.2f}%** ≥ "
                          f"{WATCH_EXIT_YELLOW_IDX}%（过缓冲带，防贴线反复）"),
            period_md=f"盘面回稳：全市场{up + down}只 {advance_ratio:.0f}%在涨、指数均{idx_avg:+.2f}%",
            advice_text="恢复正常操作，16:40收盘最终定档")
        await _push_dismiss(card)
    else:
        await _push_state_card(
            "🟡 市场风险 · 降到「谨慎」档", "orange", current_rt, YELLOW,
            [f"跌势明显缓和：{rt_line}\n——是没那么急了，不是转多。", rt_bar],
            "危险警报解除，可小仓试错、别重仓。（16:40收盘最终定档）",
            summary=f"危险降到谨慎 {advance_ratio:.0f}%在涨 指数均{idx_avg:+.2f}%")
    logger.info(f"[market_risk] watch降级 {today} {t}: {current_rt}->{target} "
                f"(涨跌比回升至 {advance_ratio:.1f}% 指数均 {idx_avg:+.2f}%)")


# ── 大盘大白话(Deploy 2B: 从 regime_filter 迁入, regime 已彻底删除) ──
# 输入 = 全市场涨跌家数/涨跌停家数(market_overview.market_stats) + 两市成交额(亿) + 风控三档。
# 与旧版差异: 「上证 vs MA20」维度去掉(那是 regime 专属数据链, 不值得为一句尾注保留一条
# 外部K线拉取), 尾注改用统一风控三档。阈值沿用 regime_filter 的评分线(涨跌停比/涨跌家数比/量能)。


def plain_market_language(market_stats: dict, amount_yi: float, state: str) -> tuple[str, str]:
    """把当前盘面翻成大白话「结论 + 操作」。纯规则纯函数, 随局面变化。"""
    up = int(market_stats.get("up_count") or 0)
    down = int(market_stats.get("down_count") or 0)
    lu = int(market_stats.get("limit_up") or 0)
    ld = int(market_stats.get("limit_down") or 0)
    if up + down <= 0:
        return "", ""
    lr = (lu / ld) if ld > 0 else (9.9 if lu > 0 else 1.0)   # 涨停/跌停比
    br = (up / down) if down > 0 else 9.9                     # 涨/跌家数比
    hot = lr >= 1.0 and lu >= 20     # 涨停不少于跌停且有一定数量(情绪热)
    cold = lr < 0.5                  # 跌停偏多
    bup = br >= 1.0                  # 个股偏多/普涨
    bdown = br < 0.5                 # 个股普跌
    panic = lr < 0.2 or br < 0.3     # 恐慌(跌停远多于涨停 / 单边踩踏)
    vp = "放量" if amount_yi >= 12000 else ("缩量" if 0 < amount_yi < 6000 else "")
    tier = _TIER_LABEL.get(state, "正常")

    # 1) 恐慌杀跌
    if panic:
        return (f"{vp}恐慌杀跌,多数个股单边下挫",
                "仓位降到2成以下或空仓,别抄底接飞刀;等跌停明显减少、出现放量止跌反包再进,手里票破位坚决止损")
    # 2) 普跌 + 跌停偏多(资金离场)
    if bdown and cold:
        return (f"{vp}普跌,资金离场、情绪偏弱",
                "仓位压到3~5成,先砍破位补不起来的弱票;不开新仓,等涨跌家数转正、风控档回正常再说")
    # 3) 分化: 强势股活跃但多数个股在跌(赚指数不赚钱)
    if hot and bdown:
        return (f"{vp}分化,赚指数不赚钱:强势股活跃但多数个股在跌",
                "只做最强主线龙头、半仓内快进快出;跟跌的弱票反弹就减、别死扛;不碰低位补涨票,这种行情它们多半继续阴跌")
    # 4) 普涨 + 情绪热
    if bup and hot:
        tail = "" if state == GREEN else f",但大盘风控仍在{tier}档"
        act = ("可加到6~8成、持股为主,优先强势板块领涨股,回踩不慌" if state == GREEN
               else "可做多但别一把满仓(6成左右),优先领涨股,留点仓位应对回踩")
        return (f"{vp}普涨,做多氛围浓、强势股活跃{tail}", act)
    # 5) 普涨但封板不强
    if bup:
        act = ("跟随放量翻红的强势股顺势持有,设好止盈,别追高位" if amount_yi >= 12000
               else "小仓参与即可,量能不足、持续性存疑,冲高见好就收、不追高")
        return (f"{vp}个股普遍翻红,但封板不强、人气一般", act)
    # 6) 个股普跌但无明显跌停
    if bdown:
        return ("个股普跌、人气偏弱",
                "仓位收到5成内、以防守为主;不抄底,等放量企稳;弱票逢反弹减、强票可留")
    # 7) 多空平衡 / 震荡
    tail = f"、大盘风控{tier}档"
    return (f"{vp}多空平衡,震荡格局{tail}",
            "区间思维、轻仓灵活;强势股回踩低吸、冲高减,别追涨杀跌;等量价突破方向明确再加仓")


async def market_plain_summary() -> dict:
    """当前大白话解读(给 /market-risk 接口): {summary, action}; 数据不足返回空串。

    成交额 = a_indices 里上证+深证 amount 之和(亿, 新浪已换算) — 原 regime_filter 同口径。"""
    try:
        overview = await repository.get_market_overview()
    except Exception:
        overview = None
    stats = (overview or {}).get("market_stats") or {}
    total_yi = 0.0
    for idx in (overview or {}).get("a_indices") or []:
        if idx.get("name") in ("上证指数", "深证成指"):
            try:
                total_yi += float(idx.get("amount") or 0)
            except (TypeError, ValueError):
                continue
    state = await get_risk_state()
    summary, action = plain_market_language(stats, total_yi, state)
    return {"summary": summary, "action": action}
