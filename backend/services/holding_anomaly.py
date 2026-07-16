"""持仓异动 检测器 + 文案 + 跟踪器(纯逻辑, 供 holding_guard tick 调用)。

5 条规则: 🔥涨停 / 🧊跌停(带封单) / ⚡急速拉升 / 🪨急速跳水 / ⚠️封板异动(封单松动 or 板上放量)+💥开板。
封板异动: 封单较峰值回落分档 或 封板期间分钟成交额放量(当前速率≥封板前基线N倍), 任一命中即报, 文案注明原因。
只提醒不下单, 不落信号库。封板/封单判定用新浪五档(卖一量=0=涨停封死, 买一量=0=跌停封死)。
设计: docs/superpowers/specs/2026-06-16-holding-anomaly-and-pool-pinyin-design.md
"""

# ── 涨跌停阈值(板别 + ST) ──
def limit_pct(code: str, name: str = "") -> float:
    if name and "ST" in name:          # *ST / ST 均含 "ST"
        return 0.05
    if code.startswith(("300", "301", "688")):
        return 0.20
    if code.startswith(("8", "43", "92", "920", "4")):   # 北交所
        return 0.30
    return 0.10


def is_limit_up(code: str, name: str, price: float, pre_close: float) -> bool:
    if not pre_close or pre_close <= 0 or not price:
        return False
    return price >= pre_close * (1 + limit_pct(code, name)) * 0.995   # 容差吸收四舍五入


def is_limit_down(code: str, name: str, price: float, pre_close: float) -> bool:
    if not pre_close or pre_close <= 0 or not price:
        return False
    return price <= pre_close * (1 - limit_pct(code, name)) * 1.005


def at_limit_side(code: str, name: str, price: float, pre_close: float):
    """价格到涨/跌停 → 'up'/'down'; 否则 None(仅看价, 不判是否封死)。"""
    if is_limit_up(code, name, price, pre_close):
        return "up"
    if is_limit_down(code, name, price, pre_close):
        return "down"
    return None


def confirm_seal(side: str, ask1_vol, bid1_vol):
    """封死确认(需五档): 涨停=卖一量0(无人卖), 跌停=买一量0。

    返回 True(封死)/False(未封死, 有对侧挂单)/None(无五档数据, 未知)。
    """
    vol = ask1_vol if side == "up" else bid1_vol
    if vol is None:
        return None
    return vol == 0


def seal_amount(quote: dict, side: str):
    """封单额(元): 涨停=买一量×买一价; 跌停=卖一量×卖一价。缺数据/0 → None。"""
    if side == "up":
        vol, px = quote.get("bid1_vol"), quote.get("bid1_price")
    else:
        vol, px = quote.get("ask1_vol"), quote.get("ask1_price")
    if not vol or not px or vol <= 0 or px <= 0:
        return None
    return float(vol) * float(px)


def seal_weaken_tier(peak: float, cur: float, tiers=(0.5, 0.75)):
    """封单较峰值回落, 返回越过的最高档(cur ≤ peak×(1−tier)); 未到任何档 → None。"""
    if not peak or peak <= 0:
        return None
    best = None
    for t in sorted(tiers):
        if cur <= peak * (1 - t):
            best = t
    return best


def amount_rate(hist, now_ts: float, window: float = 60):
    """窗口内每分钟成交额(元/分): (窗口最新累计额 − 最早累计额)/时间差 × 60。

    hist: [(ts, cum_amount)] 升序。窗口内不足两点 / 时间差≤0 → None。
    """
    pts = [(ts, amt) for ts, amt in hist if now_ts - ts <= window]
    if len(pts) < 2:
        return None
    (t0, a0), (t1, a1) = pts[0], pts[-1]
    dt = t1 - t0
    if dt <= 0:
        return None
    return max(0.0, float(a1) - float(a0)) / dt * 60


def board_volume_ratio(rate_now, rate_base):
    """板上放量倍数 = 当前分钟速率 ÷ 封板前基线速率; 基线缺失/非正 → None。"""
    if not rate_base or rate_base <= 0 or rate_now is None:
        return None
    return round(rate_now / rate_base, 2)


def surge_delta(hist, now_ts: float, now_pct: float, window: float = 180):
    """当前涨跌幅 − 约 window 秒前涨跌幅; 历史不足一个窗口 → None。

    hist: [(ts, pct)] 升序。取年龄 ≥ window×0.8 的最近一点作基准。
    """
    candidates = [(ts, pct) for ts, pct in hist if now_ts - ts >= window * 0.8]
    if not candidates:
        return None
    base_ts, base_pct = max(candidates, key=lambda x: x[0])
    return round(now_pct - base_pct, 2)


# ── 金额格式化 ──
def fmt_amount(yuan: float) -> str:
    y = float(yuan)
    if y >= 1e8:
        s = f"{y / 1e8:.2f}".rstrip("0").rstrip(".")
        return f"{s}亿"
    if y >= 1e4:
        return f"{round(y / 1e4):,}万"
    return f"{round(y):,}元"


# ── 文案构建 ──
def _holding_tag() -> str:
    return "（你的持仓）"


def _bar(fill: float, width: int = 8) -> str:
    """0~1 比例 → █░ 进度条(宽 width)。渠道通用(飞书/微信/PushPlus 文本均能渲染)。"""
    fill = max(0.0, min(1.0, fill))
    n = int(round(fill * width))
    return "█" * n + "░" * (width - n)


def build_limit_up_msg(name, code, price, pct, seal_amt, vol_ratio, amount) -> str:
    lines = [f"🔥 {name}({code}) 涨停",
             f"现价 ¥{price:.2f}　{pct:+.2f}%{_holding_tag()}"]
    if seal_amt:
        seal_line = f"封单 ¥{fmt_amount(seal_amt)}"
        if amount and amount > 0:                          # 封板强度=封单/成交额, 进度条满格=5%(铁板)
            r = seal_amt / amount
            # 分子分母都写明: 封单占成交额R%(分子) / 满格5%(分母=满格基准), 不让人猜进度条在比啥
            word = "铁板" if r >= 0.05 else ("中等" if r >= 0.01 else "偏薄")
            seal_line += f"｜封板强度 {r * 100:.1f}% {_bar(min(1.0, r / 0.05))} 满格5%·{word}"
        lines.append(seal_line)
    sub = []
    if vol_ratio:
        # 分子分母都写明: 量比X倍(分子) / 满格3倍(分母=满格基准, 异动级放量)
        vword = ("很猛" if vol_ratio >= 2.5 else "明显" if vol_ratio >= 1.5
                 else "温和" if vol_ratio >= 1.0 else "缩量")
        sub.append(f"量比 {vol_ratio:.1f}倍 {_bar(min(1.0, vol_ratio / 3))} 满格3倍·{vword}")
    if amount:
        sub.append(f"成交额 {fmt_amount(amount)}")
    if sub:
        lines.append("　".join(sub))
    lines.append("留意封板强度 / 炸板风险")
    return "\n".join(lines)


def build_limit_down_msg(name, code, price, pct, seal_amt, amount) -> str:
    line2 = f"现价 ¥{price:.2f}　{pct:+.2f}%{_holding_tag()}"
    parts = []
    if seal_amt:
        parts.append(f"封单 ¥{fmt_amount(seal_amt)}")
    if amount:
        parts.append(f"成交额 {fmt_amount(amount)}")
    line3 = ("　".join(parts) + "\n") if parts else ""
    return f"🧊 {name}({code}) 跌停\n{line2}\n{line3}留意封死强度 / 是否需要应对"


def build_surge_msg(name, code, delta, window_min, price, day_pct) -> str:
    return (f"⚡ {name}({code}) 急速拉升\n"
            f"约{window_min}分钟 {delta:+.1f}pp，现价 ¥{price:.2f}（当日 {day_pct:+.1f}%{_holding_tag()}）\n"
            f"分时快速拉伸，留意放量延续 or 冲高回落")


def build_plunge_msg(name, code, delta, window_min, price, day_pct) -> str:
    return (f"🪨 {name}({code}) 急速跳水\n"
            f"约{window_min}分钟 {delta:+.1f}pp，现价 ¥{price:.2f}（当日 {day_pct:+.1f}%{_holding_tag()}）\n"
            f"分时快速跳水，留意破位 / 恐慌杀跌")


def board_anomaly_reason(weaken: bool, surge: bool) -> str:
    """封板异动原因短语(供文案首行与推送标题)。"""
    if weaken and surge:
        return "封单减少 + 板上放量"
    if weaken:
        return "封单大幅减少"
    return "封板放量"


def build_board_anomaly_msg(name, code, side, peak_amt=None, cur_amt=None, surge_ratio=None) -> str:
    """封板异动文案: 封单松动 / 板上放量 任一或同时, 首行注明原因。

    peak_amt+cur_amt 同时给 → 含封单松动行; surge_ratio 给 → 含板上放量行。
    """
    seal_word = "涨停" if side == "up" else "跌停"
    weaken = bool(peak_amt and cur_amt)
    surge = bool(surge_ratio)
    lines = [f"⚠️ {name}({code}) {board_anomaly_reason(weaken, surge)}"]
    if weaken:
        drop = (1 - cur_amt / peak_amt) * 100
        bar = _bar(cur_amt / peak_amt if peak_amt > 0 else 0)   # 剩余封单占峰值, 越空=松动越狠
        lines.append(f"封单 ¥{fmt_amount(peak_amt)} {bar} ¥{fmt_amount(cur_amt)}（−{drop:.0f}%），仍封{seal_word}")
    if surge:
        lines.append(f"板上放量约{surge_ratio:.1f}倍于封板前，博弈加剧")
    lines.append("留意炸板风险 / 是否落袋")
    return "\n".join(lines)


def build_seal_weaken_msg(name, code, peak_amt, cur_amt, side) -> str:
    """封单松动单条文案(向后兼容; 现统一走 build_board_anomaly_msg)。"""
    return build_board_anomaly_msg(name, code, side, peak_amt=peak_amt, cur_amt=cur_amt)


def build_board_open_msg(name, code, price, pct, side) -> str:
    seal_word = "涨停" if side == "up" else "跌停"
    return (f"💥 {name}({code}) {seal_word}开板\n"
            f"现价 ¥{price:.2f}（{pct:+.1f}%，曾{seal_word}），封单已打光\n"
            f"{seal_word}被打开，留意回封 or 反向")


# ── 卡片构建(基线 v1.1): family 按异动方向 —— 涨停/急拉=opportunity(red),
#    跌停/急跌/开板=exit(green), 封板异动=risk(orange)。build_*_msg 保留为 fallback/明细同源文本。 ──

ANOMALY_FAMILY = {
    "limit_up": "opportunity", "surge": "opportunity",
    "limit_down": "exit", "plunge": "exit", "board_open": "exit",
    "board_anomaly": "risk",
}

_ANOMALY_EMOJI = {"limit_up": "🔥", "limit_down": "🧊", "surge": "⚡",
                  "plunge": "🪨", "board_anomaly": "⚠️", "board_open": "💥"}


def _anomaly_card(kind: str, name, code, *, event: str, conclusion_md: str,
                  advice_text: str, detail_md: str, fallback: str, key_fact: str = ""):
    """单条异动卡: 元素固定三件 [结论行(heading), 👉建议, 折叠明细] —— merge_anomaly_cards 依赖此结构。"""
    from backend.services import card_kit
    fam = ANOMALY_FAMILY.get(kind, "risk")
    return card_kit.Card(
        title=f"{_ANOMALY_EMOJI.get(kind, '⚠️')} {event} · {name}({code})",
        elements=[card_kit.heading_md(conclusion_md),
                  card_kit.advice(advice_text),
                  card_kit.fold(f"{event}明细", detail_md)],
        fallback=fallback, family=fam,
        summary=card_kit.summary_text(name, code, event, key_fact),
        subtitle="持仓异动提醒",
        tags=[("持仓异动", card_kit.family_template(fam))],
    )


def _detail_sans_head(msg: str) -> str:
    """msg 首行是「emoji 名称(代码) 事件」, 卡片标题已覆盖 → 明细折叠只取其余行。"""
    rest = "\n".join(msg.split("\n")[1:]).strip()
    return rest or msg


def build_limit_up_card(name, code, price, pct, seal_amt, vol_ratio, amount):
    from backend.services.card_kit import pct_md
    msg = build_limit_up_msg(name, code, price, pct, seal_amt, vol_ratio, amount)
    return _anomaly_card(
        "limit_up", name, code, event="持仓涨停",
        conclusion_md=f"涨停 ¥{price:.2f} {pct_md(pct)}",
        advice_text="留意封板强度，防炸板",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=f"¥{price:.2f} {pct:+.1f}%")


def build_limit_down_card(name, code, price, pct, seal_amt, amount):
    from backend.services.card_kit import pct_md
    msg = build_limit_down_msg(name, code, price, pct, seal_amt, amount)
    return _anomaly_card(
        "limit_down", name, code, event="持仓跌停",
        conclusion_md=f"跌停 ¥{price:.2f} {pct_md(pct)}",
        advice_text="评估应对，防封死出不去",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=f"¥{price:.2f} {pct:+.1f}%")


def build_surge_card(name, code, delta, window_min, price, day_pct):
    from backend.services.card_kit import pct_md
    msg = build_surge_msg(name, code, delta, window_min, price, day_pct)
    return _anomaly_card(
        "surge", name, code, event="急速拉升",
        conclusion_md=f"约{window_min}分钟 **{delta:+.1f}pp**，现 ¥{price:.2f}（当日 {pct_md(day_pct)}）",
        advice_text="留意放量延续或冲高回落",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=f"{window_min}分钟{delta:+.1f}pp")


def build_plunge_card(name, code, delta, window_min, price, day_pct):
    from backend.services.card_kit import pct_md
    msg = build_plunge_msg(name, code, delta, window_min, price, day_pct)
    return _anomaly_card(
        "plunge", name, code, event="急速跳水",
        conclusion_md=f"约{window_min}分钟 **{delta:+.1f}pp**，现 ¥{price:.2f}（当日 {pct_md(day_pct)}）",
        advice_text="留意破位，防恐慌杀跌",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=f"{window_min}分钟{delta:+.1f}pp")


def build_board_anomaly_card(name, code, side, peak_amt=None, cur_amt=None, surge_ratio=None):
    msg = build_board_anomaly_msg(name, code, side, peak_amt=peak_amt,
                                  cur_amt=cur_amt, surge_ratio=surge_ratio)
    reason = board_anomaly_reason(bool(peak_amt and cur_amt), bool(surge_ratio))
    seal_word = "涨停" if side == "up" else "跌停"
    return _anomaly_card(
        "board_anomaly", name, code, event="封板异动",
        conclusion_md=f"**{reason}**，仍封{seal_word}",
        advice_text="留意炸板风险，考虑落袋",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=reason)


def build_board_open_card(name, code, price, pct, side):
    from backend.services.card_kit import pct_md
    msg = build_board_open_msg(name, code, price, pct, side)
    seal_word = "涨停" if side == "up" else "跌停"
    return _anomaly_card(
        "board_open", name, code, event=f"{seal_word}开板",
        conclusion_md=f"{seal_word}开板 ¥{price:.2f} {pct_md(pct)}，封单已打光",
        advice_text="盯回封还是反向，及时应对",
        detail_md=_detail_sans_head(msg), fallback=msg,
        key_fact=f"¥{price:.2f} {pct:+.1f}%")


def merge_anomaly_cards(name, code, cards):
    """同 tick 多项异动合并卡: 结论行逐项 → 一条建议(取最重家族那项) → 明细折叠逐项。
    家族取最重: 有离场向(exit)按 exit, 否则有 risk 按 risk, 否则同首项(opportunity)。"""
    from backend.services import card_kit
    n = len(cards)
    fams = [c.family for c in cards]
    fam = "exit" if "exit" in fams else ("risk" if "risk" in fams else fams[0])
    lead = next((c for c in cards if c.family == fam), cards[0])
    elements = ([c.elements[0] for c in cards]          # 各项结论行
                + [lead.elements[1]]                    # 建议(最重那项)
                + [c.elements[2] for c in cards])       # 各项明细折叠
    events = "、".join(c.title.split(" · ")[0].split(" ", 1)[-1] for c in cards)
    fallback = (f"📣 {name}({code}) 多重异动（{n} 项）\n\n"
                + "\n\n──────────\n\n".join(c.fallback for c in cards))
    return card_kit.Card(
        title=f"📣 持仓异动 · {name}({code})",
        elements=elements, fallback=fallback, family=fam,
        summary=card_kit.summary_text(name, code, f"{n}项异动", events),
        subtitle=f"同一时点 {n} 项异动合并",
        tags=[(f"{n}项异动", card_kit.family_template(fam))],
    )


# ── 进程内跟踪器(跨日由 holding_guard tick 配合节流重置; 重启清空可接受) ──
class PctHistory:
    """每股涨跌幅时序, 供急涨/急跌速率判定。"""
    def __init__(self, keep_sec: float = 720):
        self.keep_sec = keep_sec
        self._hist: dict[str, list[tuple[float, float]]] = {}

    def push(self, code: str, ts: float, pct: float):
        seq = self._hist.setdefault(code, [])
        seq.append((ts, pct))
        cut = ts - self.keep_sec
        if seq[0][0] < cut:
            self._hist[code] = [p for p in seq if p[0] >= cut]

    def delta(self, code: str, now_ts: float, now_pct: float, window: float = 180):
        return surge_delta(self._hist.get(code, []), now_ts, now_pct, window)


class SealPeakTracker:
    """封板期间每股每方向的封单峰值。"""
    def __init__(self):
        self._peak: dict[tuple[str, str], float] = {}

    def update(self, code: str, side: str, amt: float) -> float:
        key = (code, side)
        self._peak[key] = max(self._peak.get(key, 0.0), float(amt))
        return self._peak[key]

    def peak_of(self, code: str, side: str) -> float:
        return self._peak.get((code, side), 0.0)

    def clear(self, code: str, side: str):
        self._peak.pop((code, side), None)


class AmountHistory:
    """每股累计成交额时序, 供板上放量速率判定(结构同 PctHistory)。"""
    def __init__(self, keep_sec: float = 720):
        self.keep_sec = keep_sec
        self._hist: dict[str, list[tuple[float, float]]] = {}

    def push(self, code: str, ts: float, amount: float):
        seq = self._hist.setdefault(code, [])
        seq.append((ts, float(amount)))
        cut = ts - self.keep_sec
        if seq[0][0] < cut:
            self._hist[code] = [p for p in seq if p[0] >= cut]

    def rate(self, code: str, now_ts: float, window: float = 60):
        return amount_rate(self._hist.get(code, []), now_ts, window)


class SealBaseRate:
    """封板前分钟成交额基线(每股每方向, 首次捕获后固定; 开板/跨日清空)。"""
    def __init__(self):
        self._base: dict[tuple[str, str], float] = {}

    def ensure(self, code: str, side: str, rate) -> float | None:
        key = (code, side)
        if key not in self._base and rate is not None and rate > 0:
            self._base[key] = float(rate)
        return self._base.get(key)

    def clear(self, code: str, side: str):
        self._base.pop((code, side), None)
