"""持仓守护提醒 (v1.7.x) — 只提醒不下单的盯盘守护, 合并为单一服务。

规则A 接近前高: 持仓现价逼近近60日波段阻力(下方 ≤2%)时提醒, 留意突破/受压。
规则B 盈利保护: 曾大幅获利(峰值浮盈≥+10%)又回吐到逼近成本线(当前浮盈≤+2%)时提醒,
              避免"把赚过钱的交易做成亏损离场"。回测见 bt_profit_protect.py / 记忆 project_profit-protect-backtest。
持仓异动(holding_anomaly): 🔥涨停/🧊跌停(带封单) / ⚡急速拉升/🪨急速跳水 / ⚠️封板异动(封单松动 or 板上放量)+💥开板。
              急跌/跌停与当日已发卖点去重; 封板/封单/板上放量用新浪五档+累计成交额判定。设计见 holding-anomaly spec。

设计文档: docs/superpowers/specs/2026-06-16-holding-guard-reminders-design.md
- 持仓来源: repository.get_holdings_full_info(1) → (cost_map, date_map, model_map) 三件套(FIFO 仅含未平仓)。
- 现价: data_fetcher.get_realtime_quotes(批量一次); 日K: data_fetcher.get_daily_kline(每票一次, 两规则共用)。
- 推送: notifier.send_dual(企微+飞书双推); 默认不落信号库(纯推送, 不污染胜率统计)。
- 节流: 每股每规则每日最多1次(进程内, 服务重启清当日, 提醒类容忍度高, 默认接受);
        急拉/急跌例外=每日2次且两条间隔≥30min冷却(防同一波拉升跨相邻tick背靠背连发)。
"""
import logging
import time
from datetime import date, datetime

from backend import data_fetcher
from backend.core.trading_calendar import is_workday
from backend.models import repository
from backend.services import notifier
from backend.services import holding_anomaly as ha

logger = logging.getLogger(__name__)

# ── 参数(集中常量, 便于调) ──
WINDOW_HIGH = 60        # 前高回看窗口(日)
SKIP_RECENT = 5         # 前高跳过最近 N 根
NEAR_HIGH_TOL = 0.02    # 接近前高阈值(≤2%)
PEAK_GAIN_MIN = 0.10    # 盈利保护: 峰值浮盈门槛
GIVEBACK_MAX = 0.02     # 盈利保护: 回吐触发线(当前浮盈≤+2%)
WIN_START, WIN_END = "09:25", "15:00"   # 盘中窗口
# 持仓异动(v1.7.x): 急涨/急跌速率窗口与阈值, 涨停跌停封单松动复用五档
SURGE_WIN_SEC = 180     # 急涨急跌速率窗口(约3分钟)
SURGE_PP = 3.0          # 急涨急跌阈(涨跌幅百分点)
SURGE_MAX_DAILY = 2     # 急涨急跌每股每日次数上限
SURGE_COOLDOWN_SEC = 1800  # 急涨急跌两条之间最小冷却(30分钟): 防一波连续急拉跨相邻tick把2次配额背靠背烧光(康强电子0629 14:11→14:12连发);第2条留给晚些时候真正另起的独立急拉
# 板上放量(封板期间分钟成交额放量): 当前窗口分钟速率 ÷ 封板前基线速率 ≥ 阈值 → 报
BOARD_VOL_WIN = 60      # 板上放量速率窗口(秒)
BOARD_VOL_X = 2.0       # 板上放量倍数阈值
# 持仓异动·每股每日推送总量上限(v1.7.555 批次C): 同 tick 多项异动合并成一张卡, 且全天封顶
# ANOMALY_MAX_DAILY 张, 防单股剧烈波动(涨停→封单松动→开板→急跌…)一天刷 5-8 条屏。
ANOMALY_MAX_DAILY = 3

# 动量突破类: 回踩多为健康洗盘再加速, 机械保本会砍在洗盘底(bt_profit_protect 全样 Δ−1.12%),
# 故文案附"留意而非急走"提醒。后续若中继平台突破也证实同性可加入。
MOMENTUM_BREAKOUT_MODELS = {"BUY_VOL_BREAKOUT"}

# 买点 signal_id → 文案用中文名(仅盈利保护 footer 用)
MODEL_NAMES = {
    "BUY_RALLY_MA20": "回踩MA20",
    "BUY_RALLY_MA10": "回踩MA10",
    "BUY_RALLY_MA60": "回踩MA60",
    "BUY_VOL_BREAKOUT": "缩量突破",
    "BUY_PLATFORM_BREAKOUT": "中继平台突破",
    "BUY_STRONG_START": "强势起点",
    "BUY_WEAK_EXTREME": "弱势极限",
}


# ══════════════ 纯函数(可单测, 不连库不联网) ══════════════

def prior_high(df, win: int = WINDOW_HIGH, skip: int = SKIP_RECENT):
    """近期波段前高(阻力位): 跳过最近 skip 根, 在更早 win 根窗口内取 high 最大值。

    返回 (ph, ph_date); 失效时返回 (None, None):
    - K线不足(< win+skip 根, 新股);
    - 阻力已破: 被跳过的最近 skip 根里已有 high 超过该峰, 说明近日已突破/创新高,
      这个旧高点不再是上方阻力(已变支撑), 不应再喊「接近前高」(防 京东方A/沪电股份式误报)。
    """
    if df is None or len(df) < win + skip:
        return None, None
    window = df.iloc[-(win + skip):-skip] if skip else df.iloc[-win:]
    idx = window["high"].astype(float).idxmax()
    ph = float(window.loc[idx, "high"])
    if skip:
        recent_hi = float(df.iloc[-skip:]["high"].astype(float).max())
        if recent_hi > ph:   # 近日已突破该峰 → 阻力已破, 失效
            return None, None
    return ph, str(window.loc[idx, "date"])[:10]


def is_near_high(price: float, ph: float, tol: float = NEAR_HIGH_TOL) -> bool:
    """前高下方且距离 ≤tol 触发; 已突破站上(price>ph)不报。"""
    if not ph or ph <= 0:
        return False
    return ph * (1 - tol) <= price <= ph


def compute_peak(df, entry_date, price: float) -> float:
    """建仓至今峰值价: entry_date 起子段的 high.max, 与盘中现价取大。"""
    peak = float(price)
    if df is not None and len(df) and entry_date:
        ed = str(entry_date)[:10]
        sub = df[df["date"].astype(str).str[:10] >= ed]
        if len(sub):
            peak = max(peak, float(sub["high"].astype(float).max()))
    return peak


def profit_protect_triggered(cost: float, peak: float, price: float,
                             peak_min: float = PEAK_GAIN_MIN,
                             giveback_max: float = GIVEBACK_MAX) -> bool:
    """峰值浮盈达标(确实赚过) 且 当前浮盈回吐到逼近成本线 → 触发。"""
    if not cost or cost <= 0:
        return False
    return (peak / cost - 1) >= peak_min and (price / cost - 1) <= giveback_max


def model_advisory(entry_model) -> str:
    """规则B 模型上下文附加句: 仅动量突破类附洗盘提醒, 其余/未知为空。"""
    if entry_model in MOMENTUM_BREAKOUT_MODELS:
        return "动量突破回踩常是洗盘，留意而非急走"
    return ""


# ══════════════ 文案构建(纯函数) ══════════════

def _bar(fill: float, width: int = 10) -> str:
    """0~1 比例 → █░ 进度条(宽 width)。渠道通用(飞书/微信/PushPlus 文本均能渲染)。"""
    fill = max(0.0, min(1.0, fill))
    n = int(round(fill * width))
    return "█" * n + "░" * (width - n)


def build_near_high_msg(name: str, code: str, price: float, ph: float, ph_date: str) -> tuple[str, list, str]:
    """v2 卡片版: 返回 (title, elements, fallback)。"""
    from backend.services.lark_notifier import md_element, md_table_str
    dist = price / ph - 1
    fill = 1 - min(1.0, abs(dist) / NEAR_HIGH_TOL) if NEAR_HIGH_TOL > 0 else 0
    columns = [
        {"name": "now", "display_name": "现价"},
        {"name": "high", "display_name": f"{WINDOW_HIGH}日高"},
        {"name": "dist", "display_name": "距离"},
    ]
    rows = [{"now": f"¥{price:.2f}", "high": f"¥{ph:.2f}({ph_date[5:]})", "dist": f"{dist*100:+.1f}%"}]
    elements = [
        md_element(md_table_str(columns, rows)),
        md_element(f"现├{_bar(fill)}┤前高"),
        md_element("👉 放量站上=突破，缩量到这=压力"),
    ]
    title = f"📈 {name} 接近前高"
    fallback = (f"📈 {name}({code}) 接近前高\n"
                f"现 ¥{price:.2f} → {WINDOW_HIGH}日高 ¥{ph:.2f}({ph_date[5:]}) 距{dist*100:+.1f}%")
    return title, elements, fallback


def build_profit_protect_msg(name: str, code: str, peak_gain: float, cur_gain: float,
                             cost: float, advisory: str = "", model_name: str = "") -> tuple[str, list, str]:
    """v2 卡片版: 返回 (title, elements, fallback)。"""
    from backend.services.lark_notifier import md_element, md_table_str, collapsible_element
    giveback = (peak_gain - cur_gain) / peak_gain * 100 if peak_gain > 0 else 0
    fill = cur_gain / peak_gain if peak_gain > 0 else 0
    columns = [
        {"name": "cost", "display_name": "成本"},
        {"name": "peak", "display_name": "峰值"},
        {"name": "cur", "display_name": "当前"},
        {"name": "back", "display_name": "已回吐"},
    ]
    rows = [{"cost": f"¥{cost:.2f}", "peak": f"{peak_gain*100:+.1f}%",
             "cur": f"{cur_gain*100:+.1f}%", "back": f"{giveback:.0f}%"}]
    elements = [
        md_element(md_table_str(columns, rows)),
        md_element(f"成本├{_bar(fill)}┤峰值"),
        md_element("👉 别让赚过的变成亏的，考虑锁利"),
    ]
    if model_name:
        elements.append(collapsible_element("建仓信息", f"建仓模型: {model_name}\n{advisory}" if advisory else f"建仓模型: {model_name}"))
    elif advisory:
        elements.append(collapsible_element("建仓信息", advisory))
    title = f"🛡️ {name} 盈利保护"
    fallback = (f"🛡️ {name}({code}) 盈利保护\n"
                f"峰值{peak_gain*100:+.1f}% → 现{cur_gain*100:+.1f}%（回吐{giveback:.0f}%）\n"
                f"成本 ¥{cost:.2f}")
    return title, elements, fallback


# ══════════════ 节流(进程内, 每股每规则每日一次) ══════════════

class GuardThrottle:
    """每股每规则每日计数节流; 跨日重置。throttled(limit=N) → 当日已推 ≥N 次则挡。

    向后兼容: limit 默认 1, 原 接近前高/盈利保护 的 throttled/mark 行为不变(每日1次)。
    急拉/急跌等用 limit=2, 并叠加 cooling() 冷却(两条之间需间隔最小秒数, 防同一波连发)。
    """
    def __init__(self):
        self._day = None
        self._counts: dict[tuple[str, str], int] = {}
        self._last_ts: dict[tuple[str, str], float] = {}   # 末次 mark 时间戳, 供冷却判定

    def _roll(self, today: str):
        if today != self._day:
            self._day, self._counts, self._last_ts = today, {}, {}

    def count(self, code: str, rule: str, today: str) -> int:
        self._roll(today)
        return self._counts.get((code, rule), 0)

    def throttled(self, code: str, rule: str, today: str, limit: int = 1) -> bool:
        return self.count(code, rule, today) >= limit

    def cooling(self, code: str, rule: str, now_ts: float, cooldown_sec: float) -> bool:
        """距末次推送不足 cooldown_sec → True(冷却中, 应挡)。无记录/已超冷却 → False。"""
        last = self._last_ts.get((code, rule))
        return last is not None and (now_ts - last) < cooldown_sec

    def mark(self, code: str, rule: str, today: str, ts: float | None = None):
        self._roll(today)
        self._counts[(code, rule)] = self._counts.get((code, rule), 0) + 1
        if ts is not None:
            self._last_ts[(code, rule)] = ts

    def load(self, today: str, rows: list[dict]):
        """v1.7.569: 用 DB 今日快照重建内存计数(重启后恢复, 防重推)。rows: [{code,rule,cnt,last_ts}]。"""
        self._day = today
        self._counts = {}
        self._last_ts = {}
        for r in rows:
            key = (r["code"], r["rule"])
            self._counts[key] = int(r.get("cnt") or 0)
            lt = r.get("last_ts")
            if lt is not None:
                self._last_ts[key] = float(lt)


_throttle = GuardThrottle()


async def _hydrate(today: str):
    """v1.7.569: tick 开头从 DB 恢复今日节流计数 — 盘中重启后不再重推持续成立类提醒。
    每天首个 tick(内存日切换)顺带清理 7 天前历史行。DB 失败则沿用内存态(降级不阻断)。"""
    new_day = _throttle._day != today
    try:
        from backend.models.repo import guard_throttle as _gt
        rows = await _gt.load_today(today)
        _throttle.load(today, rows)
        if new_day:
            await _gt.prune(before_days=7)
    except Exception as e:
        logger.warning(f"[holding_guard] 节流状态从DB恢复失败, 用内存态: {e}")


async def _mark(code: str, rule: str, today: str, ts: float | None = None):
    """内存 mark + 落库(v1.7.569)。DB 失败只影响跨重启去重, 本进程内存态仍生效, 不阻断推送。"""
    _throttle.mark(code, rule, today, ts=ts)
    try:
        from backend.models.repo import guard_throttle as _gt
        await _gt.bump(today, code, rule, ts)
    except Exception as e:
        logger.warning(f"[holding_guard] 节流状态落库失败({code}/{rule}): {e}")

# 持仓异动跟踪器(进程内, 重启清空可接受)
_pct_hist = ha.PctHistory()
_amt_hist = ha.AmountHistory()
_seal_peak = ha.SealPeakTracker()
_seal_base = ha.SealBaseRate()     # 封板前分钟成交额基线, 供板上放量倍数
_seal_state: dict[str, str] = {}   # code → 当前封死方向(up/down), 供开板检测


async def _send(content: str, title: str):
    try:
        await notifier.send_dual(content, lark_title=title)
    except Exception as e:
        logger.warning(f"[holding_guard] 推送失败({title}): {e}")


async def _sell_signal_today(code: str) -> bool:
    """该股今日是否已发过卖点(供急跌/跌停去重, 避免与止损/跌破MA重复)。"""
    try:
        sigs = await repository.get_today_signals(1, code)
    except Exception:
        return False
    return any(s.get("direction") in ("sell", "reduce") for s in sigs)


async def _check_anomaly(code: str, name: str, q: dict, today: str, now_ts: float):
    """持仓异动 5 规则: 急涨/急跌 + 涨停/跌停(带封单) + 封单松动/开板。"""
    price = float(q["price"])
    pre_close = float(q.get("pre_close") or 0)
    pct = float(q.get("pct_change") or 0)
    amount = float(q.get("amount") or 0)
    vol_ratio = q.get("volume_ratio")
    ask1_vol, bid1_vol = q.get("ask1_vol"), q.get("bid1_vol")
    has_l1 = "ask1_vol" in q   # 仅新浪源有五档
    win_min = SURGE_WIN_SEC // 60

    # 本 tick 命中的异动先收集, 末尾合并成一张卡发送(每股每日封顶 ANOMALY_MAX_DAILY 张)
    hits: list[tuple[str, str]] = []   # [(title, msg)]

    # ① 急涨 / 急跌(速率)
    _pct_hist.push(code, now_ts, pct)
    if amount:
        _amt_hist.push(code, now_ts, amount)   # 累计成交额时序, 供板上放量
    delta = _pct_hist.delta(code, now_ts, pct, window=SURGE_WIN_SEC)
    if delta is not None:
        # 每日上限(SURGE_MAX_DAILY) + 冷却(SURGE_COOLDOWN_SEC): 上限防总量, 冷却防同一波拉升
        #   跨相邻 tick(60s)把配额背靠背烧光 → 第2条只在距上一条 ≥30min 时才放行(独立的另一波)。
        if (delta >= SURGE_PP and not _throttle.throttled(code, "surge", today, limit=SURGE_MAX_DAILY)
                and not _throttle.cooling(code, "surge", now_ts, SURGE_COOLDOWN_SEC)):
            hits.append(("⚡ 持仓异动·急速拉升", ha.build_surge_msg(name, code, delta, win_min, price, pct)))
            await _mark(code, "surge", today, ts=now_ts)
        elif (delta <= -SURGE_PP and not _throttle.throttled(code, "plunge", today, limit=SURGE_MAX_DAILY)
                and not _throttle.cooling(code, "plunge", now_ts, SURGE_COOLDOWN_SEC)):
            if not await _sell_signal_today(code):   # 与卖点去重
                hits.append(("🪨 持仓异动·急速跳水", ha.build_plunge_msg(name, code, delta, win_min, price, pct)))
                await _mark(code, "plunge", today, ts=now_ts)

    # ② 涨停 / 跌停 + 封单松动 + 开板
    side = ha.at_limit_side(code, name, price, pre_close)
    sealed = ha.confirm_seal(side, ask1_vol, bid1_vol) if side else None
    prev = _seal_state.get(code)

    if side and sealed is not False:   # 封死, 或无五档(未知)仍报基础涨跌停
        amt = ha.seal_amount(q, side) if has_l1 else None
        rule = f"limit_{side}"
        if not _throttle.throttled(code, rule, today):
            if side == "up":
                hits.append(("🔥 持仓异动·涨停",
                             ha.build_limit_up_msg(name, code, price, pct, amt, vol_ratio, amount)))
                await _mark(code, rule, today)
            elif not await _sell_signal_today(code):   # 跌停与卖点去重
                hits.append(("🧊 持仓异动·跌停",
                             ha.build_limit_down_msg(name, code, price, pct, amt, amount)))
                await _mark(code, rule, today)
            else:
                await _mark(code, rule, today)      # 被去重抑制也记一次, 当日不再试
        # 封板异动: 封单松动 or 板上放量(任一命中即报, 文案注明原因; 均需五档)
        if has_l1:
            rate = _amt_hist.rate(code, now_ts, BOARD_VOL_WIN)
            base = _seal_base.ensure(code, side, rate)        # 首入封板时捕获基线
            surge_ratio = ha.board_volume_ratio(rate, base)
            tier = peak = None
            if amt:
                peak = _seal_peak.update(code, side, amt)
                tier = ha.seal_weaken_tier(peak, amt)
            weaken_hit = bool(tier) and not _throttle.throttled(code, f"seal_weaken_{int((tier or 0) * 100)}", today)
            vol_hit = bool(surge_ratio and surge_ratio >= BOARD_VOL_X) and not _throttle.throttled(code, "board_vol", today)
            if weaken_hit or vol_hit:
                if weaken_hit and vol_hit:
                    title = "⚠️ 持仓异动·封板异动"
                elif weaken_hit:
                    title = "⚠️ 持仓异动·封单松动"
                else:
                    title = "⚠️ 持仓异动·封板放量"
                msg = ha.build_board_anomaly_msg(
                    name, code, side,
                    peak_amt=peak if weaken_hit else None,
                    cur_amt=amt if weaken_hit else None,
                    surge_ratio=surge_ratio if vol_hit else None,
                )
                hits.append((title, msg))
                if weaken_hit:
                    await _mark(code, f"seal_weaken_{int(tier * 100)}", today)
                if vol_hit:
                    await _mark(code, "board_vol", today)
        _seal_state[code] = side
    elif prev:   # 曾封死, 现未封死 → 开板
        if not _throttle.throttled(code, "board_open", today):
            hits.append(("💥 持仓异动·开板", ha.build_board_open_msg(name, code, price, pct, prev)))
            await _mark(code, "board_open", today)
        _seal_peak.clear(code, prev)
        _seal_base.clear(code, prev)
        _seal_state.pop(code, None)

    # ── 合并发送: 本 tick 多项异动并成一张卡; 每股每日封顶 ANOMALY_MAX_DAILY 张(防刷屏) ──
    if not hits:
        return
    if _throttle.throttled(code, "anomaly_card", today, limit=ANOMALY_MAX_DAILY):
        logger.info(f"[holding_guard] {name}({code}) 当日异动卡已达上限 {ANOMALY_MAX_DAILY}, "
                    f"抑制本 tick {len(hits)} 项异动")
        return
    if len(hits) == 1:
        title, msg = hits[0]
        await _send(msg, title)
    else:   # 同 tick 多项异动(如涨停+封板放量) → 一张合并卡, 不再逐条刷屏
        combined = f"📣 {name}({code}) 多重异动（{len(hits)} 项）\n\n" + \
            "\n\n──────────\n\n".join(f"{t}\n{m}" for t, m in hits)
        await _send(combined, f"📣 持仓异动·{name}（{len(hits)}项）")
    await _mark(code, "anomaly_card", today)


# ══════════════ 任务编排(盘中 tick) ══════════════

def _natural_days_since(entry_date) -> int:
    try:
        ed = datetime.strptime(str(entry_date)[:10], "%Y-%m-%d").date()
        return (date.today() - ed).days
    except Exception:
        return 0


async def holding_guard_tick():
    """盘中(interval 60s): 真实持仓的 接近前高 / 盈利保护 守护提醒。只提醒不落库。"""
    if not is_workday():
        return
    hm = datetime.now().strftime("%H:%M")
    if not (WIN_START <= hm <= WIN_END):
        return
    today = date.today().isoformat()
    await _hydrate(today)   # v1.7.569: 从 DB 恢复今日节流计数, 防盘中重启重推

    try:
        cost_map, date_map, model_map = await repository.get_holdings_full_info(1)
    except Exception as e:
        logger.warning(f"[holding_guard] 取持仓失败: {e}")
        return
    codes = list(cost_map)
    if not codes:
        return

    # 推送偏好(mark_sold 过滤 + 卖出类快捷按钮)
    from backend.core.config import load_config
    from backend.models.repo import push_pref as pp_repo
    from backend.services import push_pref as pp
    site = (load_config().get("site_url", "") or "").rstrip("/")
    try:
        prefs = await pp_repo.active_prefs(1)
    except Exception:
        prefs = []

    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[holding_guard] 取现价失败: {e}")
        return

    now_ts = time.time()
    for code in codes:
        q = quotes.get(code)
        if not q or not q.get("price"):
            continue
        price = float(q["price"])
        name = q.get("name") or code

        # 用户已标记该票为已卖出 → 跳过所有持仓类提醒(含异动/接近前高/盈利保护)
        if pp.mark_sold_active(prefs, code):
            continue

        # 持仓异动(涨停/跌停/急涨/急跌/封单松动) — 只用实时行情, 不依赖日K, 先跑
        try:
            await _check_anomaly(code, name, q, today, now_ts)
        except Exception as e:
            logger.warning(f"[holding_guard] 异动检查失败({code}): {e}")

        entry_date = date_map.get(code)
        days = max(70, _natural_days_since(entry_date) + 10) if entry_date else 70
        try:
            df = await data_fetcher.get_daily_kline(code, days=days)
        except Exception as e:
            logger.warning(f"[holding_guard] 取日K失败({code}): {e}")
            continue
        if df is None or df.empty:
            continue

        # 规则A 接近前高
        ph, ph_date = prior_high(df)
        if ph and is_near_high(price, ph) and not _throttle.throttled(code, "prior_high", today):
            try:
                title, elements, fallback = build_near_high_msg(name, code, price, ph, ph_date)
                sold_md = pp.build_mark_sold_md(site, 1, code, name)
                if sold_md:
                    from backend.services.lark_notifier import md_element as _md_el
                    elements = list(elements) + [_md_el(sold_md)]
                    fallback += "\n" + sold_md
                await notifier.send_dual_card(fallback, lark_title=title, elements=elements, template="blue")
                await _mark(code, "prior_high", today)
            except Exception as e:
                logger.warning(f"[holding_guard] 接近前高推送失败({code}): {e}")

        # 规则B 盈利保护
        cost = cost_map.get(code)
        if cost and entry_date:
            peak = compute_peak(df, entry_date, price)
            if profit_protect_triggered(cost, peak, price) \
                    and not _throttle.throttled(code, "profit_protect", today):
                entry_model = model_map.get(code)
                title, elements, fallback = build_profit_protect_msg(
                    name, code, peak / cost - 1, price / cost - 1, cost,
                    advisory=model_advisory(entry_model),
                    model_name=MODEL_NAMES.get(entry_model, ""))
                sold_md = pp.build_mark_sold_md(site, 1, code, name)
                if sold_md:
                    from backend.services.lark_notifier import md_element as _md_el
                    elements = list(elements) + [_md_el(sold_md)]
                    fallback += "\n" + sold_md
                try:
                    await notifier.send_dual_card(fallback, lark_title=title, elements=elements, template="orange")
                    await _mark(code, "profit_protect", today)
                except Exception as e:
                    logger.warning(f"[holding_guard] 盈利保护推送失败({code}): {e}")
