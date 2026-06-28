"""Market Report — scheduled task that generates and pushes AI analysis."""

import logging
from datetime import datetime

from backend.services.ai_analyst import generate_report, TIME_SLOT_NAMES
from backend.services.notifier import send_market_report
from backend.models import repository

logger = logging.getLogger(__name__)


def _current_time_slot() -> str:
    now = datetime.now()
    t = now.strftime("%H%M")
    slots = ["0926", "1000", "1130", "1400", "1500"]
    for slot in slots:
        if abs(int(t) - int(slot)) <= 5:
            return slot
    return t[:4]


async def run_market_report():
    if datetime.now().weekday() >= 5:
        return

    time_slot = _current_time_slot()
    slot_name = TIME_SLOT_NAMES.get(time_slot, time_slot)
    logger.info(f"[MarketReport] generating report for time_slot={time_slot}")

    await repository.add_log(0, "system", "ai_report", f"触发AI市场分析：{slot_name}")

    result = await generate_report(time_slot)
    if not result:
        logger.warning("[MarketReport] no report generated")
        await repository.add_log(0, "system", "ai_report_fail", f"AI分析生成失败：{slot_name}")
        return

    content, context = result

    await repository.save_market_report(time_slot, content, context)
    logger.info(f"[MarketReport] saved to DB, time_slot={time_slot}")

    # 收盘(1500)不在此处单独推送: 由 15:05 收盘统一汇总任务(run_post_close_summary)
    # 把报告正文 + 弱势极限候选 + 盘后信号合并成一条推。其余时段照常即时推。
    if time_slot == "1500":
        logger.info("[MarketReport] 1500 收盘报告仅入库, 推送并入 15:05 收盘统一汇总")
    else:
        # 1130: 上午收盘弱势极限快照并入盘面播报, 不再单独推一条 (与 15:00→15:05 同模式)
        # 注: send_market_report 正文取自 context, 故并入须走 extra_sections(content 形参不参与推送)
        extra = ""
        if time_slot == "1130":
            try:
                from backend.services.weak_extreme_scanner import (
                    collect_weak_extreme_hits, build_weak_extreme_section,
                )
                extra = build_weak_extreme_section(await collect_weak_extreme_hits())
            except Exception as e:
                logger.warning(f"[MarketReport] 1130 弱势极限并入失败: {e}")
        await send_market_report(content, slot_name, context, extra_sections=extra)
        logger.info(f"[MarketReport] pushed to WeChat")

    await repository.add_log(0, "system", "ai_report_done", f"AI分析完成：{slot_name}，内容{len(content)}字")
