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

    # 手机友好2列: 预约披露日是核心可扫值独占列; 持仓标记+报告类型并入股票格
    # 移动优化(v1.7.581): 逐条换行文本行, 披露日(关键值)前置加粗, 名称/代码/年报类型全名换行不截
    #   (原 股票格塞 名称+代码+年报类型, 手机端字符级截断→代码/年报类型被吃掉, 年报半年报分不清)
    tlines = []
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
        tlines.append(f"**{d}**{left}　{mark}**{r['name']}**({r['code']}) {r['report_year']}{rt}")
    title = "📅 财报披露日历·近期"
    body = (f"{title}\n\n你的自选/持仓里未来{REMIND_WITHIN_DAYS}天内有 **{len(rows)}** 只披露定期报告。\n"
            "财报是二元事件——回测显示利空跌幅远大于利好涨幅,拿不准的持仓可在披露前降低仓位避险。")
    try:
        await notifier.send_dual_card(body, lark_title=title,
                                      elements=[md_element(body), md_element("\n".join(tlines))])
        logger.info(f"[disclosure] 披露提醒已推: {len(rows)} 只")
    except Exception as e:
        logger.warning(f"[disclosure] 推送失败: {e}")
