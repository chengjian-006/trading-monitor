"""每日收盘复盘摘要 — 推企微 + 飞书卡片 (v1.7.x).

内容: 今日信号数(买/卖) + 近90天买卖点胜率并排 + 表现最好/需警惕的信号。
口径同 /outcome-stats(实际收盘视角)。建议排在 outcome 回填之后跑, 让胜率含当日新到期的一批。
"""
import logging
from datetime import datetime

from backend.models import repository
from backend.services import card_kit, notifier
from backend.services.lark_notifier import md_element
from backend.core.trading_calendar import is_workday

logger = logging.getLogger(__name__)

REVIEW_USER_ID = 1   # 单用户系统: 给主账号生成复盘
COMPARE_DAYS = 90
MIN_EVAL = 5         # 进"最好/警惕"榜的最少已评估样本


def _fmt_rate(side: dict) -> str:
    if side["evaluated"] == 0:
        return f"暂无（待评估 {side['pending']}）"
    return f"{side['success_rate']}%（{side['success']}/{side['evaluated']}）"


from backend.utils.formatting import fmt_pct as _fmt_pct  # 统一百分比格式化


async def run_review_summary():
    if not is_workday():
        return
    uid = REVIEW_USER_ID
    today = datetime.now().strftime("%Y-%m-%d")

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
        buy_today, sell_today, sector_today, market_today, cmp, top, weak)
    card = card_kit.Card(
        title="📊 收盘复盘", elements=elements, fallback=text,
        family="intel", subtitle=f"近 {COMPARE_DAYS} 天实际收盘口径",
        summary=card_kit.summary_text(
            "收盘复盘", f"买{buy_today}卖{sell_today}", meta.get("buy_rate_str")),
    )
    await notifier.send_card(card)
    logger.info(f"[review_summary] 复盘摘要已推送 ({today}): 买{buy_today}卖{sell_today}"
                f"(板块{sector_today}/大盘{market_today}), 榜{len(ranked)}")


def _build_review_card(buy_today: int, sell_today: int, sector_today: int,
                       market_today: int, cmp: dict, top: list, weak: list):
    """基线 v1.1 情报卡: KPI 三栏 → 胜率强度条 → 最好/警惕全短列表格 →
    👉 一句话定性 → 折叠口径。返回 (企微文本, 飞书elements, meta)。"""
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

    # 👉 一句话定性
    if top and weak:
        adv = f"优先跟{top[0]['signal_name']}，回避低胜率信号"
    elif top:
        adv = f"优先跟{top[0]['signal_name']}，按纪律执行"
    else:
        adv = "样本不足，按交易计划执行"
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
    lines = ["【收盘复盘】", "",
             f"今日信号 买 {buy_today} / 卖 {sell_today}（个股买卖点，共 {buy_today + sell_today}）"]
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
    lines += ["", f"👉 {adv}"]

    meta = {"buy_rate_str": f"买点胜率{buy_rate}" if buy_rate else "",
            "top": top[0]["signal_name"] if top else ""}
    return "\n".join(lines), elements, meta
