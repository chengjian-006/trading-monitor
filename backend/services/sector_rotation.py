"""板块(题材)弱转强/强转弱预判 — 纯逻辑层(无 IO, 可单测)。

数据轴: 以"题材"为单位, 涨停家数为主轴(复用涨停池 limit_pool 的 reason 聚合),
辅以最高连板/炸板数/首板数刻画强弱质地。

三类产出对应三组纯函数:
  1) 盘中即时:  aggregate_themes / compute_slope / classify_intraday / detect_transition
  2) 盯盘看板:  classify_intraday 的状态直接喂看板
  3) 收盘前(14:30)次日预测: predict_next_day / is_theme_ended

注意: 所有阈值是基于经验的启发式草案, 未经回测验证, 先上线积累数据后再调参。
"""
from __future__ import annotations

# ── 盘中即时阈值 ──
HOT_LIMIT_UP = 4          # 题材涨停家数 ≥ 此值算"热门/强势"
COLD_LIMIT_UP = 1         # 题材涨停家数 ≤ 此值算"冷"
START_JUMP = 2            # 近段涨停家数净增 ≥ 此值 = 启动斜率
EBB_PEAK_RATIO = 0.5      # 当前涨停 ≤ 当日峰值 × 此比例 = 退潮

# ── 次日预测/终结阈值 ──
END_DAYS = 5              # 连续 N 个交易日彻底低迷(涨停0且无强势) → 疑似终结
REBOUND_RECENT_MAX = 1   # 近几日涨停均 ≤ 此值算"近期低迷"(弱转强反弹的前提)


def iter_themes(reason: str | None) -> list[str]:
    """涨停原因 → 该股全部题材标签(去空去重, 保序)。

    口径(v1.7.617 起): 同花顺 reason_type 是多标签(如 '仿制药+创新药+减重药'),
    一只票算进它涉及的【每一个】题材, 不再只取首段 —— 首段口径会把「创新药」不在第一位的
    涨停股漏掉(实测 20260715 创新药首段仅4只, 全标签11只), 与用户在概念板块看到的家数对不上。
    theme_heat/aggregate_themes 共用本取数, 保证今/昨基准同口径。
    """
    if not reason:
        return []
    seen, out = set(), []
    for seg in reason.split("+"):
        t = seg.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def aggregate_themes(boards: list[dict]) -> dict[str, dict]:
    """涨停池 boards → {题材: {limit_up, max_height, broken, first_board, samples}}。

    题材口径同 theme_heat: 全标签(iter_themes, reason 按 '+' 全拆)。无 reason 的票跳过。
    一只多标签票会计入它涉及的每个题材。
    """
    agg: dict[str, dict] = {}
    for b in boards or []:
        themes = iter_themes(b.get("reason"))
        if not themes:
            continue
        try:
            h = int(b.get("height") or 0)
        except (TypeError, ValueError):
            h = 0
        try:
            is_broken = int(b.get("open_times") or 0) > 0
        except (TypeError, ValueError):
            is_broken = False
        name = b.get("name") or b.get("code") or ""
        for theme in themes:
            slot = agg.setdefault(theme, {"limit_up": 0, "max_height": 0,
                                          "broken": 0, "first_board": 0, "samples": []})
            slot["limit_up"] += 1
            if h > slot["max_height"]:
                slot["max_height"] = h
            if h == 1:
                slot["first_board"] += 1
            if is_broken:
                slot["broken"] += 1
            if len(slot["samples"]) < 8:
                slot["samples"].append(name)
    return agg


def compute_slope(series: list[int], window: int = 3) -> int:
    """近段涨停家数净变化: 最后一个样本 − window 个样本前的值(不足则取首样本)。

    正=升温, 负=降温。series 为按时间升序的涨停家数序列。
    """
    if not series:
        return 0
    last = series[-1]
    ref_idx = max(0, len(series) - 1 - window)
    return last - series[ref_idx]


def classify_intraday(series: list[int], texture: dict | None = None) -> str:
    """盘中题材状态分类(供看板 + 转折判定)。

    series: 当日按时间升序的涨停家数序列(每个采样点一个值)。
    texture: 最新一档质地 {max_height, broken, first_board}(可选, 用于退潮/启动细化)。
    返回: '启动' / '升温' / '高潮' / '退潮' / '持平' / '冷' 之一。
    """
    if not series:
        return "冷"
    cur = series[-1]
    peak = max(series)
    slope = compute_slope(series)
    texture = texture or {}
    broken = int(texture.get("broken") or 0)
    early = series[: max(1, len(series) // 2)]
    was_cold = max(early) <= COLD_LIMIT_UP

    # 退潮: 当日见过热度峰值, 现明显回落, 或封板大面积松动(斜率已不再上行)
    if peak >= HOT_LIMIT_UP and (cur <= peak * EBB_PEAK_RATIO or (slope <= 0 and broken >= 3)):
        return "退潮"
    # 启动(弱转强): 早盘冷, 现涨停快速抬升
    if was_cold and cur >= HOT_LIMIT_UP - 1 and slope >= START_JUMP:
        return "启动"
    # 高潮: 高位且仍在抬升/维持
    if cur >= HOT_LIMIT_UP and slope >= 0:
        return "高潮"
    # 升温: 在涨但还没到热门门槛
    if slope >= START_JUMP and cur > COLD_LIMIT_UP:
        return "升温"
    if cur <= COLD_LIMIT_UP:
        return "冷"
    if slope > 0:
        return "升温"
    if cur >= HOT_LIMIT_UP:
        return "高潮"
    return "持平"


# ── 日基准(带昨日基准的按日口径)阈值 v1.7.x ──
COLD_BASE = 3            # 昨日涨停 ≤ 此值 = 昨日冷/温(弱转强前提; 含用户例"昨3→今10")
DAILY_JUMP = 3          # 今日 ≥ 昨日 + 此值 = 较昨明显抬升
DAILY_EBB_RATIO = 0.5   # 今日 ≤ 昨日 × 此值 = 较昨腰斩(强转弱)


def classify_daily(yest: int, cur: int, texture: dict | None = None,
                   is_afternoon: bool = False) -> str:
    """日基准题材状态: 以昨日涨停家数 yest 为基准比今日盘中 cur(用户口径: 按日比, 非日内自比)。

    yest: 昨日(上一交易日)该题材最终涨停家数; cur: 今日盘中当前涨停家数。
    is_afternoon: 是否已过 13:00(强转弱仅下午判, 防早盘涨停未封满误报退潮)。
    返回 '启动'/'退潮'/'高潮'/'升温'/'持平'/'冷'。启动/退潮 触发弱转强/强转弱推送。
    """
    # 弱转强(启动): 昨冷今热 — 昨≤2家 且 今≥4家 且 今≥昨+3, 全天可判(今天真起来就报)
    if yest <= COLD_BASE and cur >= HOT_LIMIT_UP and cur >= yest + DAILY_JUMP:
        return "启动"
    # 强转弱(退潮): 昨热今腰斩 — 昨≥4家 且 今≤昨×0.5, 仅下午判(早盘今日涨停尚未形成)
    if is_afternoon and yest >= HOT_LIMIT_UP and cur <= yest * DAILY_EBB_RATIO:
        return "退潮"
    if cur >= HOT_LIMIT_UP:
        return "高潮"          # 今日仍高位(非"昨冷今热"则归延续高潮)
    if cur > COLD_LIMIT_UP and cur > yest:
        return "升温"          # 今比昨多但没到热门门槛
    if cur <= COLD_LIMIT_UP:
        return "冷"            # 真冷: 今日涨停 ≤1 家
    # 2~3 家涨停但未超昨日: 有资金但没升温。旧版把这档也归"冷", 会出现"涨停扎堆题材(≥2家)·冷"的自相矛盾
    return "持平"


def detect_transition(prev_state: str | None, cur_state: str) -> str | None:
    """状态跃迁 → 推送类型。返回 'weak_to_strong' / 'strong_to_weak' / None。

    只在"首次进入"启动/退潮时推, 避免重复; 同状态停留不重复推。
    """
    if cur_state == prev_state:
        return None
    if cur_state == "启动":
        return "weak_to_strong"
    if cur_state == "退潮":
        return "strong_to_weak"
    return None


def is_theme_ended(daily_series: list[int], days: int = END_DAYS,
                   today_strong: bool = False) -> bool:
    """疑似终结: 最近 days 个交易日(含今日)涨停家数全 0, 且今日无强势迹象。

    daily_series: 按日期升序的每日涨停家数(末位=今日)。
    today_strong: 今日是否有强势迹象(炸板/连板/首板等), True 则不判终结。
    """
    if today_strong:
        return False
    if len(daily_series) < days:
        return False
    return all(v == 0 for v in daily_series[-days:])


def predict_next_day(daily_series: list[int], today_metrics: dict | None = None) -> dict:
    """收盘前(14:30)次日预测。

    daily_series: 按日期升序每日涨停家数(末位=今日, 来自 theme_heat + 今日盘中定版)。
    today_metrics: 今日质地 {max_height, broken, first_board}(判强势/终结)。
    返回 {direction, reason}, direction ∈
      '弱转强候选' / '强转弱候选' / '强势延续' / '弱势延续' / '疑似终结' / '中性'。
    优先级: 终结 > 强转弱 > 弱转强 > 强势延续 > 弱势延续 > 中性。
    """
    tm = today_metrics or {}
    today_strong = (int(tm.get("max_height") or 0) >= 2
                    or int(tm.get("broken") or 0) > 0
                    or int(tm.get("first_board") or 0) >= 2)
    if not daily_series:
        return {"direction": "中性", "reason": "无历史数据"}

    today = daily_series[-1]
    prior = daily_series[:-1]
    recent = prior[-3:] if prior else []
    recent_avg = sum(recent) / len(recent) if recent else 0.0
    yest = prior[-1] if prior else 0

    # 1) 疑似终结
    if is_theme_ended(daily_series, today_strong=today_strong):
        return {"direction": "疑似终结",
                "reason": f"连续{END_DAYS}日涨停归零且今日无强势, 题材大概率终结"}

    # 2) 强转弱候选: 前期热(近3日均够高), 今日见顶回落
    if recent_avg >= HOT_LIMIT_UP and today < yest and today <= recent_avg:
        return {"direction": "强转弱候选",
                "reason": f"近3日均{recent_avg:.0f}家高位, 今日{today}家较昨({yest})回落, 次日防退潮"}

    # 3) 弱转强候选: 近期低迷, 今日企稳回升
    if recent and max(recent) <= REBOUND_RECENT_MAX and today >= 2 and today > recent_avg:
        return {"direction": "弱转强候选",
                "reason": f"近3日低迷(均{recent_avg:.1f}), 今日回升至{today}家, 次日或反弹启动"}

    # 4) 强势延续: 高位且较昨持平/上行
    if today >= HOT_LIMIT_UP and today >= yest:
        return {"direction": "强势延续",
                "reason": f"今日{today}家维持高位(昨{yest}), 强势大概率延续"}

    # 5) 弱势延续: 低位且未见企稳
    if today <= COLD_LIMIT_UP and recent_avg <= COLD_LIMIT_UP:
        return {"direction": "弱势延续",
                "reason": f"今日{today}家延续低迷(近3日均{recent_avg:.1f})"}

    return {"direction": "中性", "reason": f"今日{today}家, 近3日均{recent_avg:.1f}, 方向不明"}
