"""每日收盘复盘摘要 — 推企微 + 飞书卡片 (v1.7.x).

内容: 今日信号数(买/卖) + 近90天买卖点胜率并排 + 表现最好/需警惕的信号。
口径同 /outcome-stats(实际收盘视角)。建议排在 outcome 回填之后跑, 让胜率含当日新到期的一批。
"""
import logging
from datetime import datetime

from backend.models import repository
from backend.services import notifier
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

    # 正文不再自带标题/日期: 飞书 header 已是「📋 收盘复盘」, 时间由卡片右上角时间元素统一给出,
    # 避免标题/日期在版面上重复(v1.7.407)。企微无 header, 在 wecom 文本里单独补标题兜底。
    lines = [f"**今日信号** 买 {buy_today} / 卖 {sell_today}（个股买卖点，共 {buy_today + sell_today}）"]
    extra = []
    if sector_today:
        extra.append(f"板块预警 {sector_today}")
    if market_today:
        extra.append(f"大盘风控 {market_today}")
    if extra:
        lines.append(f"另含 {' / '.join(extra)}")
    lines.append("")
    lines.append(f"**近 {COMPARE_DAYS} 天胜率（实际收盘口径）**")
    lines.append(f"🟢 买点 {_fmt_rate(cmp['buy'])}　均5日 {_fmt_pct(cmp['buy']['avg_p5'])}")
    lines.append(f"🔴 卖点 {_fmt_rate(cmp['sell'])}　均5日 {_fmt_pct(cmp['sell']['avg_p5'])}")
    if top:
        lines.append("")
        lines.append("**表现最好**")
        for x in top:
            lines.append(f"· {x['signal_name']}　{x['success_rate']}%（{x['success']}/{x['evaluated']}）")
    if weak:
        lines.append("")
        lines.append("**需警惕（胜率<50%）**")
        for x in weak:
            lines.append(f"· {x['signal_name']}　{x['success_rate']}%（{x['success']}/{x['evaluated']}）")

    body = "\n".join(lines)
    from backend.services import lark_notifier
    # 飞书走 2.0 卡: header=标题 / 右上角=时间(_time_element) / 正文=body(无标题无日期)。
    # 企微无 header, content 文本头部补标题(日期已在时间前缀里, 不再重复 · {today})。
    wecom_content = "**📋 收盘复盘**\n\n" + body
    await notifier.send_dual_card(wecom_content, lark_title="📋 收盘复盘",
                                  elements=[lark_notifier.md_element(body)])
    logger.info(f"[review_summary] 复盘摘要已推送 ({today}): 买{buy_today}卖{sell_today}"
                f"(板块{sector_today}/大盘{market_today}), 榜{len(ranked)}")
