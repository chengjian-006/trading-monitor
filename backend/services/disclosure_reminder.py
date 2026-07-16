"""财报披露日历提醒 (防御向, v1.7.573)。

回测结论: 业绩是二元事件, 利空跌幅(-2%~-2.9%)远大于利好涨幅, 拿不准就该在披露前避险。
故此提醒纯防御: 盘前告诉你「持仓/自选里哪些近期要披露定期报告」, 你自己决定要不要减仓躲事件。

两个任务:
  refresh_disclosure_calendar  每日 08:20  拉当前报告期预约披露时间表落库(慢变, 顺带捕捉变更)
  run_disclosure_reminder      交易日 08:40  自选+持仓里未来N日内披露的票 → 推「财报披露日历」卡
"""
import logging
from datetime import date, datetime, timedelta

from backend.fetcher.earnings_data import fetch_disclosure_calendar
from backend.models import repository
from backend.models.repo import earnings as earnings_repo
from backend.services import notifier
from backend.services.lark_notifier import md_element

logger = logging.getLogger(__name__)

REMIND_WITHIN_DAYS = 7      # 未来 N 个自然日内披露才提醒
REPORT_TYPE_CN = {"1": "一季报", "2": "半年报", "3": "三季报", "4": "年报"}


def _current_report_date(today: date | None = None) -> str:
    """当前处于披露窗口的报告期 = 不晚于今天的最近一个季度末(报告在期末之后披露)。"""
    t = today or date.today()
    ends = [date(t.year, 3, 31), date(t.year, 6, 30), date(t.year, 9, 30), date(t.year, 12, 31)]
    cand = [e for e in ends if e <= t]
    pe = cand[-1] if cand else date(t.year - 1, 12, 31)
    return pe.isoformat()


async def refresh_disclosure_calendar() -> None:
    """拉当前报告期预约披露时间表, upsert 落库。任意日可跑(数据慢变)。"""
    rd = _current_report_date()
    try:
        rows = await fetch_disclosure_calendar(rd)
    except Exception as e:
        logger.warning(f"[disclosure] 预约披露抓取失败({rd}): {e}")
        return
    if not rows:
        logger.info(f"[disclosure] {rd} 无预约披露数据")
        return
    await earnings_repo.upsert_disclosure(rows)
    logger.info(f"[disclosure] 预约披露刷新 {rd}: {len(rows)} 家")


async def _user_codes() -> list[str]:
    """自选 + 持仓的股票代码(去重)。"""
    codes = set()
    try:
        codes.update(await repository.list_quotable_codes())
    except Exception:
        pass
    try:
        cost_map, _, _ = await repository.get_holdings_full_info(1)
        codes.update(cost_map.keys())
    except Exception:
        pass
    return sorted(codes)


def build_disclosure_card(rows: list[dict], hold_codes: set, today: date | None = None):
    """财报披露日历 → 基线 v1.1 结构卡(情报族 blue):
    结论行 → 全短列表(股票|披露日|类型, >8行 Top5+全量折叠) → 👉防御建议 → 折叠(全量+回测依据)。"""
    from backend.services import card_kit

    today = today or date.today()
    trows: list[tuple] = []
    long_lines: list[str] = []   # 长值(代码/报告年度/剩余天数)下沉折叠
    for r in rows:
        held = str(r["code"]) in hold_codes
        rt = REPORT_TYPE_CN.get(str(r["report_type"]), "定期报告")
        d = str(r["appoint_date"])[:10]
        try:
            dleft = (date.fromisoformat(d) - today).days
        except Exception:
            dleft = ""
        mark = "🔴" if held else ""
        left = f" · {dleft}天后" if dleft != "" else ""
        trows.append((f"{mark}{r['name']}", d[5:10], rt))
        long_lines.append(f"**{d}**{left}　{mark}**{r['name']}**({r['code']}) {r['report_year']}{rt}")

    elements: list = [md_element(
        f"自选/持仓未来 **{REMIND_WITHIN_DAYS}** 天内 **{len(rows)}** 只披露定期报告（🔴=持仓）")]
    headers = ["股票", "披露日", "类型"]
    if len(trows) > 8:
        elements.append(card_kit.short_table(headers, trows[:5]))
        elements.append(md_element(f"…等 **{len(trows)}** 只，全量见折叠"))
    else:
        elements.append(card_kit.short_table(headers, trows))
    elements.append(card_kit.advice("拿不准的持仓，披露前先减仓避险"))
    elements.append(card_kit.fold(
        "全部名单与回测依据",
        "\n".join(long_lines) +
        "\n\n财报是二元事件——回测显示利空跌幅(-2%~-2.9%)远大于利好涨幅，"
        "拿不准就该在披露前避险；要不要减由你决定。"))

    fallback = (f"📅 财报披露日历\n\n你的自选/持仓里未来{REMIND_WITHIN_DAYS}天内有 **{len(rows)}** 只披露定期报告。\n"
                "财报是二元事件——回测显示利空跌幅远大于利好涨幅,拿不准的持仓可在披露前降低仓位避险。\n\n"
                + "\n".join(long_lines))
    return card_kit.Card(
        title=f"📅 财报披露日历 · {len(rows)}只", elements=elements, fallback=fallback,
        family="intel",
        summary=card_kit.summary_text("财报披露日历", f"{len(rows)}只",
                                      f"最近{trows[0][1]}" if trows else ""),
        subtitle=f"未来{REMIND_WITHIN_DAYS}天窗口")


async def run_disclosure_reminder() -> None:
    """交易日盘前: 自选+持仓里未来 REMIND_WITHIN_DAYS 日内披露定期报告的票 → 推提醒卡。"""
    from backend.core.trading_calendar import is_workday
    if not is_workday():
        return
    codes = await _user_codes()
    if not codes:
        return
    today = date.today()
    end = today + timedelta(days=REMIND_WITHIN_DAYS)
    try:
        rows = await earnings_repo.upcoming_disclosures(codes, today.isoformat(), end.isoformat())
    except Exception as e:
        logger.warning(f"[disclosure] 查询待披露失败: {e}")
        return
    if not rows:
        return

    hold_codes = set()
    try:
        cost_map, _, _ = await repository.get_holdings_full_info(1)
        hold_codes = set(cost_map.keys())
    except Exception:
        pass

    try:
        await notifier.send_card(build_disclosure_card(rows, hold_codes, today))
        logger.info(f"[disclosure] 披露提醒已推: {len(rows)} 只")
    except Exception as e:
        logger.warning(f"[disclosure] 推送失败: {e}")
