"""晚盘复盘总结 — 推企微 + 飞书卡片 (v1.7.x).

一张 19:00 晚盘卡, 把原先散在 14:40/15:05/19:00/早08:40 的盘后内容收敛成三段:
  💼 持仓今日表现 : 逐票今日涨跌 + 浮盈(相对摊薄成本), 跌得多的在前
  📊 今日信号复盘 : 今日买/卖点数 + 近90天买卖点胜率 + 表现最好/需警惕的信号(实际收盘口径)
  📅 近期财报披露 : 自选/持仓未来7天要披露定期报告的票(从早盘披露卡挪来, 晚上提前一天知道·防御)
口径同 /outcome-stats(实际收盘视角)。排在 outcome 回填之后跑, 让胜率含当日新到期的一批。
v1.7.651: 原 14:40尾盘决策/15:05盘后汇总/08:40披露卡下线, 披露内容并入本卡(用户拍板精简盘后推送)。
"""
import logging
from datetime import date, datetime, timedelta

from backend import data_fetcher
from backend.models import repository
from backend.services import card_kit, notifier
from backend.services.lark_notifier import md_element
from backend.core.trading_calendar import is_workday

logger = logging.getLogger(__name__)

REVIEW_USER_ID = 1   # 单用户系统: 给主账号生成复盘
COMPARE_DAYS = 90
MIN_EVAL = 5         # 进"最好/警惕"榜的最少已评估样本
HOLD_TOP = 6         # 持仓表现表最多直显行数, 超出折叠
DISC_WITHIN_DAYS = 7  # 近期披露窗口(自然日)
REPORT_TYPE_CN = {"1": "一季报", "2": "半年报", "3": "三季报", "4": "年报"}


def _fmt_rate(side: dict) -> str:
    if side["evaluated"] == 0:
        return f"暂无（待评估 {side['pending']}）"
    return f"{side['success_rate']}%（{side['success']}/{side['evaluated']}）"


from backend.utils.formatting import fmt_pct as _fmt_pct  # 统一百分比格式化


async def _collect_holdings_perf(uid: int) -> list[dict]:
    """逐票今日涨跌 + 浮盈(现价/摊薄成本-1)。晚盘 quotes 返回当日收盘价, 即"今日表现"。
    跌得多的排前(风险优先)。查失败/空仓返回 []。"""
    try:
        cost_map, _, _ = await repository.get_holdings_full_info(uid)
    except Exception as e:
        logger.warning(f"[review_summary] 持仓取数失败: {e}")
        return []
    if not cost_map:
        return []
    codes = list(cost_map.keys())
    try:
        quotes = await data_fetcher.get_realtime_quotes(codes)
    except Exception as e:
        logger.warning(f"[review_summary] 持仓行情失败: {e}")
        quotes = {}
    out: list[dict] = []
    for code, cost in cost_map.items():
        q = quotes.get(code) or {}
        price = float(q.get("price") or 0)
        pct = float(q.get("pct_change") or 0)
        floating = (price / cost - 1) * 100 if (price and cost) else None
        out.append({"code": code, "name": q.get("name") or code,
                    "price": price, "pct": pct, "floating": floating})
    out.sort(key=lambda x: x["pct"])   # 今日跌幅大的在前
    return out


async def _collect_disclosures() -> tuple[list[dict], set]:
    """自选+持仓未来 DISC_WITHIN_DAYS 日内要披露定期报告的票 + 持仓代码集(标🔴)。"""
    from backend.services.disclosure_reminder import _user_codes
    from backend.models.repo import earnings as earnings_repo
    codes = await _user_codes()
    if not codes:
        return [], set()
    today = date.today()
    end = today + timedelta(days=DISC_WITHIN_DAYS)
    try:
        rows = await earnings_repo.upcoming_disclosures(codes, today.isoformat(), end.isoformat())
    except Exception as e:
        logger.warning(f"[review_summary] 待披露查询失败: {e}")
        rows = []
    hold_codes: set = set()
    try:
        cost_map, _, _ = await repository.get_holdings_full_info(REVIEW_USER_ID)
        hold_codes = set(cost_map.keys())
    except Exception:
        pass
    return rows, hold_codes


async def run_review_summary():
    if not is_workday():
        return
    uid = REVIEW_USER_ID
    today = datetime.now().strftime("%Y-%m-%d")

    hold_perf = await _collect_holdings_perf(uid)
    disc_rows, disc_hold = await _collect_disclosures()

    todays = await repository.get_today_signals(uid)
    # 四分类口径(v1.7.x): 「买/卖」只数个股买卖点; 板块预警(BK*)、大盘风控(plunge)单列,
    # 避免「资金回流·板块预警」「大盘退潮·减仓提示」被旧的二分法误算进「买」(虚高买点数)。
    buy_today = sell_today = sector_today = market_today = 0
    for s in todays:
        code = str(s.get("code") or "")
        direction = str(s.get("direction") or "").lower()
        if code.startswith("BK"):
            sector_today += 1
        elif direction in ("sell", "reduce"):
            sell_today += 1
        elif direction == "buy":
            buy_today += 1
        else:  # plunge 等 = 大盘退潮/风控提示
            market_today += 1

    cmp = await repository.get_outcome_compare(uid, days_back=COMPARE_DAYS)
    stats = await repository.get_signal_outcome_stats(uid, days_back=COMPARE_DAYS)
    ranked = sorted(
        [v for v in stats.values() if v.get("evaluated", 0) >= MIN_EVAL],
        key=lambda x: x["success_rate"], reverse=True,
    )
    top = ranked[:3]
    weak = [x for x in ranked if x["success_rate"] < 50][::-1][:3]

    text, elements, meta = _build_review_card(
        buy_today, sell_today, sector_today, market_today, cmp, top, weak,
        hold_perf=hold_perf, disc_rows=disc_rows, disc_hold=disc_hold)
    bits = [f"持仓{len(hold_perf)}只" if hold_perf else "",
            f"买{buy_today}卖{sell_today}",
            f"披露{len(disc_rows)}只" if disc_rows else ""]
    card = card_kit.Card(
        title="📊 晚盘复盘总结", elements=elements, fallback=text,
        family="intel", subtitle="持仓表现 · 信号胜率 · 近期披露",
        summary=card_kit.summary_text("晚盘复盘", *[b for b in bits if b]),
    )
    await notifier.send_card(card)
    logger.info(f"[review_summary] 晚盘复盘已推送 ({today}): 持仓{len(hold_perf)}"
                f"/买{buy_today}卖{sell_today}(板块{sector_today}/大盘{market_today})"
                f"/披露{len(disc_rows)}/榜{len(ranked)}")


def _fmt_signed(v) -> str:
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else "-"


def _hold_color(v) -> str | None:
    if not isinstance(v, (int, float)):
        return None
    return "red" if v > 0 else ("green" if v < 0 else None)


def _build_holdings_elements(hold_perf: list) -> tuple[list, list]:
    """💼 持仓今日表现: 汇总行 + 全短列表(股票|今日|浮盈), 超 HOLD_TOP 行折叠。
    返回 (飞书elements, 企微文本行)。"""
    n = len(hold_perf)
    up = sum(1 for h in hold_perf if h["pct"] > 0)
    down = sum(1 for h in hold_perf if h["pct"] < 0)
    head = f"💼 **持仓今日表现**　{n}只（{up}涨 {down}跌）"
    els: list = [md_element(head)]
    rows = [(h["name"], f"<font color='{_hold_color(h['pct']) or 'grey'}'>{_fmt_signed(h['pct'])}</font>",
             f"<font color='{_hold_color(h['floating']) or 'grey'}'>{_fmt_signed(h['floating'])}</font>")
            for h in hold_perf]
    headers = ["股票", "今日", "浮盈"]
    if n > HOLD_TOP:
        els.append(card_kit.short_table(headers, rows[:HOLD_TOP]))
        els.append(md_element(f"…等 **{n}** 只，全量见券商"))
    else:
        els.append(card_kit.short_table(headers, rows))
    tlines = [f"【持仓今日表现】{n}只（{up}涨{down}跌）"]
    for h in hold_perf[:HOLD_TOP]:
        tlines.append(f"· {h['name']}　今日{_fmt_signed(h['pct'])}　浮盈{_fmt_signed(h['floating'])}")
    return els, tlines


def _build_disclosure_elements(disc_rows: list, disc_hold: set) -> tuple[list, list]:
    """📅 近期财报披露: 复用 disclosure_reminder 口径, 嵌入本卡的一段(结论行+全短表)。
    返回 (飞书elements, 企微文本行)。"""
    today = date.today()
    trows: list[tuple] = []
    tlines = [f"【近期披露】未来{DISC_WITHIN_DAYS}天 {len(disc_rows)}只（🔴=持仓）"]
    for r in disc_rows:
        held = str(r["code"]) in disc_hold
        rt = REPORT_TYPE_CN.get(str(r["report_type"]), "定期报告")
        d = str(r["appoint_date"])[:10]
        mark = "🔴" if held else ""
        trows.append((f"{mark}{r['name']}", d[5:10], rt))
        tlines.append(f"· {d[5:10]}　{mark}{r['name']}　{rt}")
    els: list = [md_element(
        f"📅 **近期财报披露**　未来 **{DISC_WITHIN_DAYS}** 天 **{len(disc_rows)}** 只（🔴=持仓，披露前拿不准可减仓避险）")]
    headers = ["股票", "披露日", "类型"]
    if len(trows) > 8:
        els.append(card_kit.short_table(headers, trows[:5]))
        els.append(md_element(f"…等 **{len(trows)}** 只"))
    else:
        els.append(card_kit.short_table(headers, trows))
    return els, tlines


def _build_review_card(buy_today: int, sell_today: int, sector_today: int,
                       market_today: int, cmp: dict, top: list, weak: list,
                       hold_perf: list | None = None, disc_rows: list | None = None,
                       disc_hold: set | None = None):
    """基线 v1.1 情报卡: KPI 三栏 → 💼持仓今日表现 → 📊胜率强度条 → 最好/警惕全短列表格 →
    📅近期披露 → 👉 一句话定性 → 折叠口径。返回 (企微文本, 飞书elements, meta)。"""
    hold_perf = hold_perf or []
    disc_rows = disc_rows or []
    disc_hold = disc_hold or set()
    buy_s, sell_s = cmp["buy"], cmp["sell"]

    def _rate_str(side: dict) -> str | None:
        return f"{side['success_rate']}%" if side.get("evaluated") else None

    buy_rate = _rate_str(buy_s)
    # 结论区: KPI 三栏(今日买点/今日卖点/买点胜率)
    elements: list = [card_kit.kpi_row([
        ("今日买点", f"{buy_today}个", "red" if buy_today else None),
        ("今日卖点", f"{sell_today}个", "green" if sell_today else None),
        (f"买点胜率{COMPARE_DAYS}天", buy_rate or "暂无",
         ("red" if buy_s["success_rate"] >= 50 else "green") if buy_rate else None),
    ])]

    # 💼 持仓今日表现(空仓跳过)
    hold_tlines: list = []
    if hold_perf:
        h_els, hold_tlines = _build_holdings_elements(hold_perf)
        elements += h_els

    # 数据区 1: 买/卖点胜率强度条(≤8 格) + 均5日
    def _rate_line(icon: str, label: str, side: dict, color: str) -> str:
        if not side.get("evaluated"):
            return f"{icon} {label} 暂无（待评估 {side.get('pending', 0)}）"
        bar = card_kit.strength_bar(side["success_rate"] / 100,
                                    f"{side['success_rate']}%", color=color)
        avg = (card_kit.pct_md(side["avg_p5"], bold=False)
               if isinstance(side.get("avg_p5"), (int, float)) else "-")
        return (f"{icon} {label} {bar}（{side['success']}/{side['evaluated']}）"
                f"　均5日 {avg}")

    elements.append(md_element("\n".join([
        _rate_line("🟢", "买点", buy_s, "red"),
        _rate_line("🔴", "卖点", sell_s, "green"),
    ])))

    # 数据区 2: 表现最好 / 需警惕(全短列: 信号 | 胜率 | 战绩)
    if top:
        elements.append(md_element("**表现最好**"))
        elements.append(card_kit.short_table(
            ["信号", "胜率", "战绩"],
            [(x["signal_name"], f"<font color='red'>**{x['success_rate']}%**</font>",
              f"{x['success']}/{x['evaluated']}") for x in top]))
    if weak:
        elements.append(md_element("**需警惕（胜率<50%）**"))
        elements.append(card_kit.short_table(
            ["信号", "胜率", "战绩"],
            [(x["signal_name"], f"<font color='green'>**{x['success_rate']}%**</font>",
              f"{x['success']}/{x['evaluated']}") for x in weak]))

    # 📅 近期财报披露(无则跳过)
    disc_tlines: list = []
    if disc_rows:
        d_els, disc_tlines = _build_disclosure_elements(disc_rows, disc_hold)
        elements += d_els

    # 👉 一句话定性
    if top and weak:
        adv = f"优先跟{top[0]['signal_name']}，回避低胜率信号"
    elif top:
        adv = f"优先跟{top[0]['signal_name']}，按纪律执行"
    else:
        adv = "样本不足，按交易计划执行"
    if disc_rows:
        adv += "；披露前拿不准的持仓可减仓避险"
    elements.append(card_kit.advice(adv))

    # 折叠: 口径 + 今日其他信号计数
    fold_lines = [
        f"📐 口径：近 {COMPARE_DAYS} 天实际收盘视角；进「最好/警惕」榜需已评估 ≥ {MIN_EVAL} 笔；"
        "均5日=触发后第 5 个交易日平均涨跌。",
    ]
    extra = []
    if sector_today:
        extra.append(f"板块预警 {sector_today}")
    if market_today:
        extra.append(f"大盘风控 {market_today}")
    if extra:
        fold_lines.append(f"今日另含 {' / '.join(extra)}（不计入个股买卖点）。")
    elements.append(card_kit.fold("口径说明", "\n".join(fold_lines)))

    # 企微纯文本兜底(同源信息量)
    lines = ["【晚盘复盘总结】", ""]
    if hold_tlines:
        lines += hold_tlines + [""]
    lines += [f"今日信号 买 {buy_today} / 卖 {sell_today}（个股买卖点，共 {buy_today + sell_today}）"]
    if extra:
        lines.append(f"另含 {' / '.join(extra)}")
    lines.append("")
    lines.append(f"近 {COMPARE_DAYS} 天胜率（实际收盘口径）")
    lines.append(f"🟢 买点 {_fmt_rate(buy_s)}　均5日 {_fmt_pct(buy_s['avg_p5'])}")
    lines.append(f"🔴 卖点 {_fmt_rate(sell_s)}　均5日 {_fmt_pct(sell_s['avg_p5'])}")
    if top:
        lines.append("")
        lines.append("表现最好")
        for x in top:
            lines.append(f"· {x['signal_name']}　{x['success_rate']}%（{x['success']}/{x['evaluated']}）")
    if weak:
        lines.append("")
        lines.append("需警惕（胜率<50%）")
        for x in weak:
            lines.append(f"· {x['signal_name']}　{x['success_rate']}%（{x['success']}/{x['evaluated']}）")
    if disc_tlines:
        lines += [""] + disc_tlines
    lines += ["", f"👉 {adv}"]

    meta = {"buy_rate_str": f"买点胜率{buy_rate}" if buy_rate else "",
            "top": top[0]["signal_name"] if top else ""}
    return "\n".join(lines), elements, meta
