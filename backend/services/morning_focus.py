"""盘前「今日关注」摘要卡 (机制类, cron 交易日 08:50) — 开盘前该知道的合并成一屏。

一张蓝色情报卡(基线 v1.1 五区骨架), 只取系统内现成数据(全部读库/内存函数, 不拉外部接口):
  - KPI 三栏: 持仓N只 / 昨日触发信号M条 / 今日财报披露K家(自选+持仓)
  - 昨日信号跟踪: 昨日买点触发的票(股票|模型短名|昨收涨跌), >5 只 Top5+等N只(全量折叠)
  - 今日风险一行: 今日披露财报的自选/持仓摘要(取数复用 disclosure_reminder 的 upcoming_disclosures;
    盘前速览今日披露; 近期全窗口披露见 19:00 晚盘复盘总结的近期披露段)
  - 当前生效状态: 大盘风险档(market_risk_controller) / 止损压力票数(stop_escalation
    活跃升级 episode) / 到线提醒生效订阅数(push_pref ma_alert_*)
  - 👉 一句话; 空数据段跳过; 全空(无持仓无信号无披露)不发

防重复划界: 晚盘复盘总结(review_summary 19:00)看"持仓表现+胜率战绩+近期披露"、竞价播报(09:26)看"今早盘面"、
持仓研判晚报(holding_brief 20:00)是"逐票深度" —— 本卡只做 08:50 盘前"清单式速览"
(昨日追踪+今日日程+当前状态), 不算胜率、不看盘面、不展开明细。

每日一次去重: cfzy_biz_guard_throttle 哨兵行(code=SYS, rule=morning_focus), 重启不重发;
学 stop_escalation "先标记再推"(防发送成功后标记失败 → 重启重发)。
"""
import logging
from datetime import date

from backend.core.trading_calendar import is_workday, prev_trading_day
from backend.models.repo import guard_throttle as gt
from backend.services import card_kit, notifier
from backend.services.lark_notifier import md_element

logger = logging.getLogger(__name__)

FOCUS_USER_ID = 1     # 单用户系统: 给主账号生成盘前速览
TOP_N = 5             # 昨日买点表格只放 Top5, 其余"等N只"+全量折叠(基线名单>8行规则从紧)

# 每日一次去重哨兵(guard_throttle 通用 (trade_date, code, rule) 设计, 同 stop_escalation 复用法)
_DEDUP_CODE = "SYS"
_DEDUP_RULE = "morning_focus"

RISK_LABEL = {"GREEN": ("🟢", "正常"), "YELLOW": ("🟡", "谨慎"), "RED": ("🔴", "高风险")}


# ══════════════ 纯函数(可单测, 不连库) ══════════════

def model_short(name, maxlen: int = 8) -> str:
    """模型全名 → 表格用短名: 剥括号后缀(（右侧）等), 截 maxlen 字防表格挤爆。
    全名重点展示是信号卡自己的事(结论区加粗), 这里是聚合速览只要认得出。"""
    s = str(name or "").split("（")[0].split("(")[0].strip()
    return s[:maxlen]


def close_pct(closes_desc) -> float | None:
    """[昨收, 前收] → 昨日收盘涨跌%; 数据不足或前收非正返回 None。"""
    if not closes_desc or len(closes_desc) < 2:
        return None
    try:
        c0, c1 = float(closes_desc[0]), float(closes_desc[1])
    except (TypeError, ValueError):
        return None
    if c1 <= 0:
        return None
    return (c0 / c1 - 1) * 100


def build_morning_focus_card(*, holding_n: int, total_signals: int, buy_rows: list,
                             disclosure_rows: list, hold_codes: set,
                             risk_state: str = "GREEN", risk_since: str = "",
                             stop_pressure_n: int = 0, ma_alert_n: int = 0):
    """盘前速览 → 基线 v1.1 情报卡(blue): KPI三栏 → 昨日买点表 → 今日披露一行 →
    当前状态 → 👉一句话 → 折叠(超5只全量)。全空(无持仓无信号无披露)返回 None 不发。

    buy_rows = [{name, code, model(全名), pct(昨收涨跌%或None)}, ...]
    disclosure_rows = earnings_repo.upcoming_disclosures 行(code/name/report_type/appoint_date)
    """
    k = len(disclosure_rows)
    if holding_n <= 0 and total_signals <= 0 and k <= 0:
        return None

    elements: list = [card_kit.kpi_row([
        ("持仓", f"{holding_n}只"),
        ("昨日信号", f"{total_signals}条", "red" if total_signals else None),
        ("今日披露", f"{k}家", "orange" if k else None),
    ])]
    fb = ["📊 今日关注（盘前速览）", "",
          f"持仓 {holding_n} 只 / 昨日信号 {total_signals} 条 / 今日披露 {k} 家"]

    # ── 昨日买点追踪段(空则跳过) ──
    fold_detail = ""
    if buy_rows:
        elements.append(md_element(f"**昨日买点追踪** 共 **{len(buy_rows)}** 只"))
        trows = [(r["name"], model_short(r["model"]),
                  card_kit.pct_md(r["pct"], bold=False) if r.get("pct") is not None else "—")
                 for r in buy_rows]
        headers = ["股票", "模型", "昨收"]
        if len(trows) > TOP_N:
            elements.append(card_kit.short_table(headers, trows[:TOP_N]))
            elements.append(md_element(f"…等 **{len(trows)}** 只，全量见折叠"))
            fold_detail = "\n".join(
                f"**{r['name']}**({r['code']}) {r['model']}"
                + (f"　昨收 {r['pct']:+.1f}%" if r.get("pct") is not None else "")
                for r in buy_rows)
        else:
            elements.append(card_kit.short_table(headers, trows))
        fb.append("")
        fb.append(f"昨日买点追踪（{len(buy_rows)} 只）")
        fb += [f"· {r['name']}({r['code']}) {model_short(r['model'])}"
               + (f" 昨收{r['pct']:+.1f}%" if r.get("pct") is not None else "")
               for r in buy_rows[:TOP_N]]
        if len(buy_rows) > TOP_N:
            fb.append(f"…等 {len(buy_rows)} 只")

    # ── 今日风险段: 今日披露一句摘要(盘前速览; 近期全窗口披露见19:00晚盘复盘总结) ──
    if k:
        names = "、".join(
            f"{'🔴' if str(r.get('code')) in hold_codes else ''}{r.get('name')}"
            for r in disclosure_rows[:5])
        more = f" 等{k}家" if k > 5 else ""
        line = f"📅 今日披露财报：{names}{more}（🔴=持仓），披露前拿不准可减仓避险"
        elements.append(md_element(line))
        fb += ["", line]

    # ── 当前生效状态段(风险档常显; 止损压力/到线提醒空则省行) ──
    icon, lab = RISK_LABEL.get(risk_state, RISK_LABEL["GREEN"])
    status = [f"{icon} 大盘风险 **{lab}**"
              + (f"（{risk_since} 起）" if risk_since and risk_state != "GREEN" else "")]
    if stop_pressure_n:
        status.append(f"🚨 止损压力中 **{stop_pressure_n}** 只（升级卡开盘 09:30 见）")
    if ma_alert_n:
        status.append(f"🔔 到线提醒生效 **{ma_alert_n}** 单")
    elements.append(md_element("\n".join(status)))
    fb += [""] + [s.replace("**", "") for s in status]

    # ── 👉 一句话 + 折叠 ──
    advice_text = "先看昨日信号追踪，9:26竞价播报见"
    elements.append(card_kit.advice(advice_text))
    fb += ["", f"👉 {advice_text}"]
    if fold_detail:
        elements.append(card_kit.fold(f"昨日买点全量（{len(buy_rows)}只）", fold_detail))

    return card_kit.Card(
        title="📊 今日关注", elements=elements, fallback="\n".join(fb), family="intel",
        summary=card_kit.summary_text(
            "今日关注", f"持仓{holding_n}只", f"昨日信号{total_signals}条",
            f"今日披露{k}家" if k else ""),
        subtitle="盘前速览 · 昨日追踪+今日日程+当前状态")


# ══════════════ 取数(全部系统内现成数据, 不拉外部接口) ══════════════

async def _collect() -> dict:
    """凑齐构卡入参; 各段独立容错(单段失败不拖垮整卡, 空段构卡时自动跳过)。"""
    from backend.models import repository
    from backend.models.repo import earnings as earnings_repo
    from backend.models.repo import push_pref as pref_repo
    from backend.models.repo._db import _fetchall
    from backend.services import market_risk_controller
    from backend.services import push_pref as pref_svc
    from backend.services import stop_escalation as se
    from backend.services.disclosure_reminder import _user_codes

    today = date.today()

    # 持仓只数(真实持仓 = 净持股>0)
    hold_codes: set = set()
    try:
        qty_map = await repository.get_holdings_qty(FOCUS_USER_ID)
        hold_codes = {c for c, q in qty_map.items() if q > 0}
    except Exception as e:
        logger.warning(f"[morning_focus] 取持仓失败: {e}")

    # 昨日(上一交易日)信号 + 其中的个股买点
    prev = prev_trading_day(today)
    sigs: list = []
    try:
        sigs = await repository.get_signals_history(
            FOCUS_USER_ID, limit=500, date=prev.isoformat())
    except Exception as e:
        logger.warning(f"[morning_focus] 取昨日信号失败: {e}")
    buys = [s for s in sigs
            if str(s.get("direction") or "").lower() == "buy"
            and str(s.get("code") or "").isdigit()]

    # 昨收涨跌: kline_cache 昨日/前日两天收盘价(现成缓存, 覆盖不到的票显示"—")
    pct_map: dict[str, float] = {}
    codes = sorted({str(s["code"]) for s in buys})
    if codes:
        prev2 = prev_trading_day(prev)
        try:
            ph = ",".join(["%s"] * len(codes))
            rows = await _fetchall(
                f"SELECT code, trade_date, close FROM cfzy_sys_kline_cache "
                f"WHERE code IN ({ph}) AND trade_date IN (%s, %s)",
                (*codes, prev.isoformat(), prev2.isoformat()))
            by_code: dict[str, dict] = {}
            for r in rows:
                by_code.setdefault(str(r["code"]), {})[str(r["trade_date"])[:10]] = r["close"]
            for c, m in by_code.items():
                pct = close_pct([m.get(prev.isoformat()), m.get(prev2.isoformat())]) \
                    if prev.isoformat() in m and prev2.isoformat() in m else None
                if pct is not None:
                    pct_map[c] = pct
        except Exception as e:
            logger.warning(f"[morning_focus] 取昨收涨跌失败: {e}")
    buy_rows = [{"name": s.get("name") or str(s["code"]), "code": str(s["code"]),
                 "model": s.get("signal_name") or s.get("signal_id") or "",
                 "pct": pct_map.get(str(s["code"]))} for s in buys]

    # 今日披露财报(自选+持仓; 取数逻辑复用 disclosure_reminder)
    disclosure_rows: list = []
    try:
        u_codes = await _user_codes()
        if u_codes:
            disclosure_rows = await earnings_repo.upcoming_disclosures(
                u_codes, today.isoformat(), today.isoformat())
    except Exception as e:
        logger.warning(f"[morning_focus] 取今日披露失败: {e}")

    # 大盘风险档(带 since 锚点)
    risk_state, risk_since = "GREEN", ""
    try:
        risk_state, risk_since = await market_risk_controller.get_risk_state_info()
    except Exception as e:
        logger.warning(f"[morning_focus] 取风险档失败: {e}")

    # 止损压力中的票数 = stop_escalation 活跃升级 episode(曾发红卡且未解除)
    stop_n = 0
    try:
        for c in await gt.recent_rule_codes(se._GT_ESC, days=7):
            esc = await gt.last_date(c, se._GT_ESC)
            dis = await gt.last_date(c, se._GT_DISMISSED)
            if esc and not (dis and dis >= esc):
                stop_n += 1
    except Exception as e:
        logger.warning(f"[morning_focus] 取止损压力失败: {e}")

    # 到线提醒生效订阅数(push_pref ma_alert_*, SQL 层已滤未撤销+未过期)
    ma_n = 0
    try:
        ma_n = len(await pref_repo.active_prefs_of_kinds(list(pref_svc.MA_ALERT_KINDS)))
    except Exception as e:
        logger.warning(f"[morning_focus] 取到线订阅失败: {e}")

    return dict(holding_n=len(hold_codes), total_signals=len(sigs), buy_rows=buy_rows,
                disclosure_rows=disclosure_rows, hold_codes=hold_codes,
                risk_state=risk_state, risk_since=risk_since,
                stop_pressure_n=stop_n, ma_alert_n=ma_n)


# ══════════════ 编排(cron 交易日 08:50) ══════════════

async def run_morning_focus() -> None:
    """交易日 08:50: 盘前「今日关注」速览卡, 每日一次(DB 去重, 重启不重发), 全空不发。"""
    if not is_workday():
        return
    today = date.today().isoformat()
    try:
        if await gt.last_date(_DEDUP_CODE, _DEDUP_RULE) == today:
            logger.info("[morning_focus] 今日已发过, 跳过")
            return
    except Exception as e:
        # 学 market_ebb: 去重查询失败保守跳过(宁可缺一张不重复轰炸)
        logger.error(f"[morning_focus] 去重查询失败, 本轮跳过: {e}")
        return

    data = await _collect()
    card = build_morning_focus_card(**data)
    if card is None:
        logger.info("[morning_focus] 无持仓无信号无披露, 全空不发")
        return

    # 先标记再推(学 stop_escalation._send_dismiss: 防推送成功后标记失败 → 重启重发)
    await gt.bump(today, _DEDUP_CODE, _DEDUP_RULE, None)
    try:
        await notifier.send_card(card)
        logger.info(f"[morning_focus] 盘前今日关注已推: 持仓{data['holding_n']}只 "
                    f"昨日信号{data['total_signals']}条 披露{len(data['disclosure_rows'])}家")
    except Exception as e:
        logger.warning(f"[morning_focus] 推送失败: {e}")
