"""股票池自定义预警检测 (v1.7.x).

交易时段按"股票池扫描"同节奏运行:
  1. 取全用户 enabled+active 的预警(JOIN 股票池拿实时价/涨跌幅)
  2. 按 code 批量取 K线收盘 → 算 MA5/10/20/60(完整日线均值, 口径同 quote_refresher)与昨收
  3. 逐条预警求值(内部多条件 AND), 命中 → 标记一次性失效 + 按用户聚合
  4. 逐用户把命中的多条聚合成一张卡, 推到该用户自己的企微/飞书 webhook

设计取舍:
  - 现价/涨跌幅用 quote_refresher 已落库的实时值, 不额外拉行情接口(防加重数据源/封IP)。
  - MA 用 kline_cache 完整日线收盘均值(与股票池"贴近均线"筛选同口径); 当日 bar 盘中通常未入缓存,
    故 closes[0] 视作昨收。数据不足的条件一律判为"不满足", 绝不误触发。
  - 一次性触发: 命中即 status=triggered 失效, 需用户手动重启。
"""
import logging
from collections import defaultdict

from backend.core.trading_calendar import is_trading_time
from backend.models import repository
from backend.models.repo import alerts as alerts_repo

logger = logging.getLogger(__name__)

_MA_KEYS = (5, 10, 20, 60)
# 各均线最少需要的收盘根数(不足则该均线判为缺失 → 相关条件不满足), 口径同 quote_refresher._ma
_MA_MIN = {5: 4, 10: 7, 20: 15, 60: 40}
_OP_LABEL = {"gte": "≥", "lte": "≤"}


def _ma(closes: list[float], n: int) -> float | None:
    """最近 n 根日收盘均值; 不足 _MA_MIN[n] 根 → None。"""
    if not closes:
        return None
    avail = closes[:n]
    if len(avail) < _MA_MIN.get(n, n):
        return None
    return sum(avail) / len(avail)


def build_ctx(price, pct_change, closes: list[float]) -> dict:
    """把一只票的现价/涨跌幅/近 N 日收盘 → 求值上下文。closes 为最近在前(DESC)。"""
    return {
        "price": float(price) if price not in (None, 0) else None,
        "pct_change": float(pct_change) if pct_change is not None else None,
        "prev_close": float(closes[0]) if closes else None,
        "ma": {n: _ma(closes, n) for n in _MA_KEYS},
    }


def _eval_one(cond: dict, ctx: dict) -> bool:
    """单个条件求值。所需数据缺失一律 False(不误触发)。"""
    dim = cond.get("dim")
    price = ctx.get("price")
    if dim == "price":
        if price is None:
            return False
        v = cond.get("value")
        if v is None:
            return False
        return price >= v if cond.get("op") == "gte" else price <= v
    if dim == "pct":
        pct = ctx.get("pct_change")
        v = cond.get("value")
        if pct is None or v is None:
            return False
        return pct >= v if cond.get("op") == "gte" else pct <= v
    if dim == "ma_near":
        ma = ctx.get("ma", {}).get(cond.get("ma"))
        band = cond.get("band")
        if price is None or ma is None or not ma or band is None:
            return False
        return abs(price - ma) / abs(ma) * 100 <= band
    if dim == "ma_cross":
        ma = ctx.get("ma", {}).get(cond.get("ma"))
        prev = ctx.get("prev_close")
        if price is None or ma is None or prev is None:
            return False
        if cond.get("dir") == "up":
            return prev < ma <= price
        return prev > ma >= price
    return False


def eval_conditions(conditions: list, ctx: dict) -> bool:
    """整条预警: 至少一个条件且全部满足(AND)才触发。"""
    if not conditions:
        return False
    return all(_eval_one(c, ctx) for c in conditions)


def describe_condition(cond: dict) -> str:
    """单条件中文摘要(推送/前端复用口径)。"""
    dim = cond.get("dim")
    if dim == "price":
        return f"价格{_OP_LABEL.get(cond.get('op'), '')}{cond.get('value')}"
    if dim == "pct":
        return f"涨跌幅{_OP_LABEL.get(cond.get('op'), '')}{cond.get('value')}%"
    if dim == "ma_near":
        return f"接近MA{cond.get('ma')}(±{cond.get('band')}%)"
    if dim == "ma_cross":
        return f"{'上穿' if cond.get('dir') == 'up' else '跌破'}MA{cond.get('ma')}"
    return "?"


def describe_hit(it: dict) -> str:
    """命中一条预警的大白话描述(推送正文用)。均线快捷预设带上均线实际值对比;
    普通自定义退回条件摘要。重要数字加粗。"""
    preset = it.get("preset") or ""
    ma_val = it.get("ma_value")
    if preset.startswith("ma") and ma_val:
        n = preset[2:]
        return (f"股价碰到{n}日线：现价 **{it['price']:.2f}**，MA{n} **{ma_val:.2f}**"
                f"（±0.5%以内算碰线，今天不再重复报）")
    return f"满足: {describe_conditions(it['conditions'])}"


def describe_conditions(conditions: list) -> str:
    parts = [describe_condition(c) for c in (conditions or [])]
    return " 且 ".join(parts) if parts else "-"


async def check_custom_alerts():
    if not is_trading_time():
        return

    rows = await alerts_repo.list_active_alerts()
    if not rows:
        return

    codes = sorted({r["code"] for r in rows})
    try:
        closes_map = await repository.fetch_kline_close_batch(codes, 60)
    except Exception as e:
        logger.warning(f"[custom_alert] 取K线收盘失败, 本轮跳过: {e}")
        return

    triggered_by_user: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        price = r.get("price")
        if price in (None, 0):
            continue  # 停牌/无现价 → 跳过, 不触发
        ctx = build_ctx(price, r.get("pct_change"), closes_map.get(r["code"]) or [])
        conditions = r.get("conditions") or []
        try:
            hit = eval_conditions(conditions, ctx)
        except Exception as e:
            logger.debug(f"[custom_alert] 求值异常 alert={r.get('id')}: {e}")
            continue
        if not hit:
            continue
        repeat_daily = bool(r.get("repeat_daily"))
        await alerts_repo.mark_triggered(r["id"], float(price), repeat_daily=repeat_daily)
        # 均线快捷预设: 带上均线实际值, 推送里直接给"现价 vs 均线"对比
        preset = r.get("preset") or ""
        ma_value = None
        if preset.startswith("ma"):
            try:
                ma_value = ctx["ma"].get(int(preset[2:]))
            except (ValueError, KeyError):
                ma_value = None
        triggered_by_user[r["user_id"]].append({
            "code": r["code"],
            "name": r.get("name") or r["code"],
            "price": float(price),
            "pct_change": r.get("pct_change"),
            "conditions": conditions,
            "note": r.get("note") or "",
            "preset": preset,
            "repeat_daily": repeat_daily,
            "ma_value": ma_value,
        })

    for user_id, items in triggered_by_user.items():
        try:
            await _push_user_alerts(user_id, items)
        except Exception as e:
            logger.warning(f"[custom_alert] 推送失败 user={user_id}: {e}")

    total = sum(len(v) for v in triggered_by_user.values())
    if total:
        logger.info(f"[custom_alert] 触发 {total} 条, 覆盖 {len(triggered_by_user)} 个用户")


def _fmt_pct(pct) -> str:
    if pct is None:
        return "-"
    return f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"


async def _push_user_alerts(user_id: int, items: list[dict]):
    """把某用户本轮触发的多条预警聚合成一张卡, 发到该用户自己的企微/飞书。"""
    from backend.services import notifier, lark_notifier

    user = await repository.get_user_by_id(user_id)
    if not user:
        return
    # 飞书 webhook 已统一为单一全局配置(不再读用户个人配置)
    from backend.core.config import load_config
    _cfg = load_config()
    lark_webhook = _cfg.get("lark_webhook", "")
    lark_on = bool(_cfg.get("lark_enabled", False))

    head = f"【自定义预警】触发 {len(items)} 条" if len(items) > 1 else "【自定义预警】触发"
    lines = [head, ""]
    rows_tbl = []
    has_once = has_daily = False
    for it in items:
        note = f" · {it['note']}" if (it["note"] and not it.get("preset")) else ""
        lines.append(f"▲ **{it['name']}({it['code']})**{note}")
        lines.append(f"   现价 **{it['price']:.2f}**（{_fmt_pct(it.get('pct_change'))}） — {describe_hit(it)}")
        if it.get("repeat_daily"):
            has_daily = True
        else:
            has_once = True
        rows_tbl.append({
            "stock": f"{it['name']}({it['code']})",
            "cond": describe_conditions(it["conditions"]),
            "price": f"{it['price']:.2f}",
            "pct": _fmt_pct(it.get("pct_change")),
        })
    lines.append("")
    foot = []
    if has_once:
        foot.append("一次性预警已自动停用, 需要可在股票池重新启用")
    if has_daily:
        foot.append("均线提醒每天最多报一次, 明天自动继续盯")
    lines.append(f"({'; '.join(foot)})")
    content = "\n".join(lines)

    elements = []
    if lark_on and lark_webhook:
        # 手机友好2列: 现价+涨跌合成核心可扫列; 触发条件已在正文逐条列出, 不塞长内容进单元格
        columns = [
            {"name": "stock", "display_name": "股票", "data_type": "text", "width": "55%"},
            {"name": "price", "display_name": "现价(涨跌)", "data_type": "text", "width": "45%"},
        ]
        md_rows = [{"stock": r["stock"], "price": f"{r['price']}（{r['pct']}）"} for r in rows_tbl]
        elements = [lark_notifier.md_table(columns, md_rows)]

    await notifier.send_dual_card_to(
        content,
        lark_title="🔔 自定义预警",
        elements=elements,
        lark_webhook=lark_webhook, lark_on=lark_on,
    )
