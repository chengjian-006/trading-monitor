"""止损强制升级 (v1.7.x) — 堵"知道该砍却不砍"的执行漏洞。

背景: 硬止损信号(弱势极限-12% / 浮亏止损-5/8/10%)已按日重复触发, 但普通推送卡片会被
      告警疲劳划过去(阳光电源6月-12%喊到-24%扛了16条信号)。本模块把"连续多日未执行的
      止损"从普通卡片升级为 🚨 红卡, 单独发、带累计多亏、开盘+午间各推一次, 直到熄火。

判定"未执行": 交割单 T+1 导入, 后端无法盘中知道是否已卖; 以"持仓仍在 + 硬止损信号连续
      触发天数"为代理 —— 卖了导入后持仓消失, 止损不再触发, 升级自动熄火。

范围(硬止损 signal_id): SELL_WEAK_STOP / SELL_LOSS_5 / SELL_LOSS_8 / SELL_LOSS_10。
      跌破MA(SELL_BREAK_MA*)、止盈减仓 属软信号, 不升级(免滥用稀释)。

熄火(任一): ① 持仓消失(卖了) ② 现价站回首次止损价上方 ③ 用户点"当日/本周不提醒"(stop_snooze)。

本模块纯逻辑(连续天数/累计多亏/站回判定/文案)可单测; DB 取信号持仓 + 定时编排在下半段。
"""
from __future__ import annotations

# 纳入升级的硬止损 signal_id(与 signal_specs 的被动止损子集一致, 去掉跌破MA)
HARD_STOP_IDS = ("SELL_WEAK_STOP", "SELL_LOSS_5", "SELL_LOSS_8", "SELL_LOSS_10")

ESCALATE_N_DAYS = 2   # 连续 ≥N 个交易日仍触发 → 升级


# ══════════════ 纯函数(可单测) ══════════════

RECENT_GRACE_DAYS = 2   # 连续段须触及最近 N 个交易日, 否则视为陈旧不升级

def consecutive_stop_days(fire_dates, trading_days_desc: list[str],
                          recent_grace: int = RECENT_GRACE_DAYS) -> int:
    """止损连续未执行的交易日数 = 最近一段"连续触发"的交易日长度。

    fire_dates: 该股硬止损信号触发过的日期集合(YYYY-MM-DD)。
    trading_days_desc: 交易日列表, 最新在前(覆盖回看窗口)。

    从最新交易日往回走: 跳过开头未触发的日(今日扫描早于触发/今日价在止损位上方),
    命中首个触发日后连续计数, 一遇未触发日即断(价格曾站回=旧段作废, 不续)。

    防陈旧(recent_grace): 连续段必须触及最近 recent_grace 个交易日 —— 允许今日尚未触发
    (扫描早于触发/今日价在止损位上方), 但若最近这几天全无触发, 说明是半个月前的陈旧历史
    连续段(价格早已站回), 不该升级(沪电股份 06-15 案例), 返回 0。
    """
    fire = set(fire_dates)
    # 连续段起点必须落在最近 recent_grace 个交易日内
    recent = set(trading_days_desc[:recent_grace])
    if not (fire & recent):
        return 0
    run = 0
    started = False
    for d in trading_days_desc:
        if d in fire:
            run += 1
            started = True
        elif started:
            break
    return run


def should_escalate(run_days: int, n: int = ESCALATE_N_DAYS) -> bool:
    return run_days >= n


def extra_loss(first_stop_price: float, current_price: float, qty: int) -> int:
    """若首次止损位就砍, 相比现在少亏的金额(元)。正=扛着多亏了; 负=现价已高于首次止损位。"""
    return round((first_stop_price - current_price) * qty)


def price_recovered(current_price: float, first_stop_price: float) -> bool:
    """现价站回首次止损价上方 → 熄火。"""
    return current_price > first_stop_price


def build_escalation_card(*, name: str, code: str, day_n: int,
                          first_stop_date: str, first_stop_price: float, first_stop_pct: float,
                          current_price: float, current_pct: float, extra_loss_yuan: int,
                          actions_md: str = "") -> tuple[str, str]:
    """升级红卡 (title, body_md)。body 走 lark_md(飞书/微信通用)。"""
    title = f"🚨 止损未执行·第{day_n}天 · {name}"
    lines = [
        f"**{name}({code})**",
        f"首次止损 {first_stop_date[5:]} @{first_stop_price:.2f}（{first_stop_pct:+.0f}%）未执行",
        f"现价 {current_price:.2f}　当前 **{current_pct:+.1f}%**",
        "━━━━━━━━━━━━━━",
        f"若首次止损执行，已少亏 **¥{extra_loss_yuan:,.0f}**",
    ]
    if actions_md:
        lines.append(actions_md)
    return title, "\n".join(lines)


def recent_trading_days_desc(n: int = 30, today=None) -> list[str]:
    """最近 n 个交易日(YYYY-MM-DD), 最新在前。剔周末+法定节假日(走 is_workday)。"""
    from datetime import date as _date, datetime as _dt, timedelta
    from backend.core.trading_calendar import is_workday
    d = today or _date.today()
    out: list[str] = []
    probe = d
    guard = 0
    while len(out) < n and guard < n * 3 + 20:
        if is_workday(_dt(probe.year, probe.month, probe.day)):
            out.append(probe.isoformat())
        probe = probe - timedelta(days=1)
        guard += 1
    return out


# ══════════════ 编排(定时 09:30 / 11:20) ══════════════

import logging  # noqa: E402

logger = logging.getLogger(__name__)


async def stop_escalation_tick():
    """定时(09:30 / 11:20)扫真实持仓的硬止损未执行升级。只推送, 不落信号库(不污染胜率)。"""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    from backend.core.config import load_config
    from backend.models import repository
    from backend.services import push_pref as pp
    from backend.services import notifier
    from backend.models.repo import push_pref as pp_repo
    from backend import data_fetcher

    user_id = 1
    try:
        cost_map, date_map, _ = await repository.get_holdings_full_info(user_id)
        qty_map = await repository.get_holdings_qty(user_id)
    except Exception as e:
        logger.warning(f"[stop_escalation] 取持仓失败: {e}")
        return
    codes = [c for c in qty_map if qty_map.get(c, 0) > 0]
    if not codes:
        return

    try:
        prefs = await pp_repo.active_prefs(user_id)
    except Exception:
        prefs = []
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[stop_escalation] 取现价失败: {e}")
        return

    tdays = recent_trading_days_desc(30)
    site = (load_config().get("site_url", "") or "").rstrip("/")

    for code in codes:
        # 用户已标记该票为已卖出 → 跳过所有卖出/持仓类提醒
        if pp.mark_sold_active(prefs, code):
            continue
        # 用户已 stop_snooze 这只票的止损升级 → 跳过(不影响其它推送)
        if pp.stop_snooze_active(prefs, code):
            continue
        q = quotes.get(code)
        if not q or not q.get("price"):
            continue
        price = float(q["price"])
        name = q.get("name") or code

        try:
            fires = await repository.get_stop_fires_by_code(code, list(HARD_STOP_IDS), user_id, days=30)
        except Exception as e:
            logger.warning(f"[stop_escalation] 取止损信号失败({code}): {e}")
            continue
        if not fires:
            continue

        fire_dates = {str(f["d"])[:10] for f in fires}
        run = consecutive_stop_days(fire_dates, tdays)
        if not should_escalate(run):
            continue

        first = fires[0]                                   # 最早一条 = 首次止损
        first_price = float(first.get("price") or 0)
        if first_price <= 0:
            continue
        # 熄火②: 现价已站回首次止损价上方
        if price_recovered(price, first_price):
            continue

        qty = int(qty_map.get(code, 0))
        cost = cost_map.get(code)
        cur_pct = (price / cost - 1) * 100 if cost and cost > 0 else 0.0
        first_pct = (first_price / cost - 1) * 100 if cost and cost > 0 else 0.0
        loss = extra_loss(first_price, price, qty)
        actions = pp.build_stop_escalation_actions_md(site, user_id, code) if site else ""
        sold_md = pp.build_mark_sold_md(site, user_id, code, name)
        if sold_md:
            actions = (actions + "　·　" + sold_md) if actions else sold_md

        title, body = build_escalation_card(
            name=name, code=code, day_n=run,
            first_stop_date=str(first["d"])[:10], first_stop_price=first_price, first_stop_pct=first_pct,
            current_price=price, current_pct=cur_pct, extra_loss_yuan=loss, actions_md=actions)
        try:
            await notifier.send_dual(body, lark_title=title)
        except Exception as e:
            logger.warning(f"[stop_escalation] 推送失败({code}): {e}")
