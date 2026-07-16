# -*- coding: utf-8 -*-
"""「分时二波过前高」纯检测器 (v1.7.597) — 只提醒不落信号库。

形态(严格·二波过前高): 逐分钟分时序列上,
  ① 第一波: 盘中放量冲出当日高点 H1
  ② 回落降温: H1 之后回落 ≥ pullback_min(缩量整理)
  ③ 第二波: 最近 leg_window 分钟放量(分钟量 ≥ vol_mult × 当日基准分钟量)拉升 ≥ leg_rise_min
  ④ 过前高: 现价创当日新高(> H1), 且新高就发生在这最近 leg_window 分钟(此刻正在突破)
  确认后报: ①②③④ 全中才返回(非二波刚冒头就报)。

设计与回测背书见记忆 second-surge-backtest: 裸形态edge弱, 反直觉最优=浅回落/温和放量/早盘中盘;
用户拍板仍按此原设计(忠实呈现"二次放量拉涨"的抬头提示, 非买点), 参数全可调。

纯函数(零外部请求): 供 second_surge_scanner 读分时缓存后逐票调用, 亦供回测。
trends 元素: {"time": "YYYY-MM-DD HH:MM", "price": float, "volume": float}(分钟量, 非累计)。
"""
import statistics

from backend.utils.limit_calc import is_at_limit_up

# 默认参数(= 回测之前原设计, 用户0708拍板; 反直觉最优参数见记忆 second-surge-backtest, 可按需调这里)。
# 检测器读 leg_*/vol_mult/pullback_min/min_points/chase_limit_buffer_pct; 其余是扫描器层闸门。
DEFAULT_PARAMS = {
    "enabled": True,
    "leg_window": 4,               # 第二波拉升测量窗(分钟) L, 兼作"过前高新鲜窗"
    "leg_rise_min": 0.008,         # 第二波涨幅下限 P2 (0.8%)
    "vol_mult": 1.8,               # 第二波放量倍数 K (×当日基准分钟量)
    "pullback_min": 0.015,         # 两波之间回落降温 D (1.5%)
    "min_points": 15,              # 至少多少分钟数据才判(约09:45)
    "chase_limit_buffer_pct": 1.0, # 现价距涨停板 ≤ 此% 视为逼近涨停不报
    "min_amount_now": 50_000_000,  # 触发时累计成交额下限(元, 流动性; 扫描器层用trends估算)
    "start_minute": 585,           # 09:45 起触发(需时间形成第一波+回落; 分钟序号=时*60+分)
    "include_index": True,         # 含概念指数(用户0708: 在池即扫不排除)
    "require_ma20_up": True,       # 20日均线须温和上翘才报(扫描器层闸门, 见 ma20_rising)
    "ma20_up_lookback": 3,         # 「上翘」回看天数: 昨收MA20 ≥ 该天数前的MA20(走平也算)
}


def ma20_rising(closes_desc: list[float], lookback: int = 3) -> bool:
    """20日均线温和上翘: 昨收算的 MA20 ≥ lookback 日前的 MA20(走平也算, 只滤明确掉头)。

    closes_desc = 不含今日的历史日线收盘(最新在前); 需 ≥ 20+lookback 根才判, 不足视为不满足。
    刻意只用【已收盘】日线判趋势结构, 绝不掺今日盘中现价 —— 二波过前高当天股价本就是涨的,
    若把今日价算进 MA20 则几乎必然上翘, 闸门形同虚设(见记忆 second-surge-backtest)。
    """
    lb = max(1, int(lookback))
    need = 20 + lb
    if len(closes_desc) < need:
        return False
    ma_now = sum(closes_desc[:20]) / 20            # 昨收算的 MA20
    ma_prev = sum(closes_desc[lb:lb + 20]) / 20    # lb 日前算的 MA20
    return ma_now >= ma_prev


def cum_amount(trends: list[dict]) -> float:
    """截至最新的当日累计成交额(元)。

    优先用分时自带的真实成交额 amount(THS 分时第3字段, 单位元); 老缓存无该字段时退回
    量×价估算 —— volume 单位是「股」不是「手」, 不要再 ×100(曾放大百倍, 致 min_amount
    闸门实际只卡百分之一)。
    """
    if any(t.get("amount") for t in trends):
        return sum(float(t.get("amount") or 0) for t in trends)
    return sum((float(t.get("volume") or 0) * float(t.get("price") or 0)) for t in trends)


def wave_amounts(trends: list[dict]) -> list[float]:
    """逐分钟成交额(元)序列, 与 trends 等长。资金强度诸口径的公共取数。"""
    if any(t.get("amount") for t in trends):
        return [float(t.get("amount") or 0) for t in trends]
    return [float(t.get("volume") or 0) * float(t.get("price") or 0) for t in trends]


_SURGE_TAGLINE = "抬头看一眼·形态提示非买卖建议(历史多为隔日T+1~T+3小余温, 当日≈走平)。"


def surge_section(name: str, code: str, r: dict, action_md: str = "") -> str:
    """单只二波过前高的卡片区块(逐分钟文本行版, 手机端不截)。action_md=逐票静音链接行(可空)。"""
    lines = [
        f"**{name}({code})**　现价 ¥{r['price_now']:.2f}（{r['day_pct']:+.1f}%）",
        f"　突破第一波高点 ¥{r['H1']:.2f}（{r['h1_time']}冲高 → 回落 -{r['trough_pct']:.1f}% → "
        f"二波放量 {r['vol_mult']:.1f}× 拉升 +{r['leg_rise_pct']:.1f}% 创当日新高）",
        f"　[📈 看分时图](https://stockpage.10jqka.com.cn/{code}/)",
    ]
    if action_md:
        lines.append("　" + action_md)
    return "\n".join(lines)


def build_surge_card(items: list[dict]) -> tuple[str, str]:
    """合并二波过前高提醒卡 (title, body_md)。items 每项 {name, code, r, action_md}。
    同一tick多只触发合并成一张卡(防突发刷屏); 定性文案不诱导当日买卖。"""
    secs = [surge_section(it["name"], it["code"], it["r"], it.get("action_md", "")) for it in items]
    n = len(items)
    title = f"🔥 二波过前高 · {items[0]['name']}({items[0]['code']})" if n == 1 else f"🔥 二波过前高 · {n}只"
    body = "\n\n".join(secs) + "\n\n" + _SURGE_TAGLINE
    return title, body


def baseline_vol(vols: list[float]) -> float:
    """当日基准分钟量 = 逐分钟量中位数(跳开盘首根竞价/开盘尖峰, 中位数抗放量脉冲)。
    有效点(>0)不足 3 个 → 0(交给上层判无效)。"""
    body = [v for v in vols[1:] if v and v > 0]
    if len(body) < 3:
        return 0.0
    return float(statistics.median(body))


def detect_second_surge(trends: list[dict], pre_close: float, params: dict,
                        code: str | None = None, name: str = "") -> dict | None:
    """检测「二波过前高」。命中返回特征 dict, 否则 None。

    params: leg_window(L,分钟) / leg_rise_min(二波涨幅) / vol_mult(放量倍数K) /
            pullback_min(回落降温D) / min_points(最少分钟数) / chase_limit_buffer_pct(逼近涨停容差%)。
    """
    L = int(params.get("leg_window", 4))
    min_points = int(params.get("min_points", 15))
    n = len(trends)
    if n < min_points or n < L + 3 or not pre_close or pre_close <= 0:
        return None

    prices = [float(t["price"]) for t in trends]
    vols = [float(t.get("volume") or 0) for t in trends]
    price_now = prices[-1]
    if price_now <= 0:
        return None

    leg_start = n - 1 - L                      # 二波窗口的基点(此前=第一波+回落区)
    if leg_start < 2:
        return None

    # ④ 过前高: 现价须是当日新高, 且突破由当前这波拉起(H1=二波之前的最高)
    day_high = max(prices)
    if price_now < day_high * 0.999:           # 现价不在当日新高附近 → 非"此刻正在突破"
        return None
    H1 = max(prices[:leg_start + 1])
    if price_now <= H1:                        # 没过第一波高点
        return None
    h1_idx = prices[:leg_start + 1].index(H1)

    # ③ 第二波放量拉升(最近 L 分钟)
    base_p = prices[leg_start]
    if base_p <= 0:
        return None
    leg_rise = (price_now - base_p) / base_p
    if leg_rise < float(params.get("leg_rise_min", 0.008)):
        return None
    vbar = baseline_vol(vols)
    if vbar <= 0:
        return None
    leg_vols = [v for v in vols[leg_start + 1:] if v > 0]
    if not leg_vols:
        return None
    vol_mult = (sum(leg_vols) / len(leg_vols)) / vbar
    if vol_mult < float(params.get("vol_mult", 1.8)):
        return None

    # ② 回落降温: H1 之后到二波基点之间回落 ≥ D
    trough = min(prices[h1_idx:leg_start + 1])
    pullback = (H1 - trough) / H1 if H1 > 0 else 0.0
    if pullback < float(params.get("pullback_min", 0.015)):
        return None

    # 逼近涨停不报(收盘价≈涨停价挂不进; 回测无 code 自动跳过, 口径与其它右侧买点一致)
    day_pct = (price_now - pre_close) / pre_close * 100
    if code and is_at_limit_up(code, day_pct, name, tol=float(params.get("chase_limit_buffer_pct", 1.0))):
        return None

    return {
        "price_now": round(price_now, 2),
        "H1": round(H1, 2),
        "h1_time": trends[h1_idx]["time"][-5:],      # HH:MM
        "trough_pct": round(pullback * 100, 2),      # 中间回落深度%
        "leg_rise_pct": round(leg_rise * 100, 2),    # 二波拉升幅度%
        "vol_mult": round(vol_mult, 2),              # 二波放量倍数
        "day_pct": round(day_pct, 2),                # 触发时当日涨幅%
    }
