# -*- coding: utf-8 -*-
"""14:40 尾盘决策合并卡 — 真假强势评分 + 次日板块预测 + 弱势极限尾盘候选 合成一张卡 (v1.7.554).

原三条独立推送(strength_quality_1430@14:30 / sector_next_day_predict@14:30 /
weak_extreme_1445@14:45)在 14:30~14:45 挤成三条, 现合并为一张 14:40 推送。
各部分独立计算、任一失败仍发其余; 全空则不发。
"""
import asyncio
import logging

from backend.core.trading_calendar import is_workday
from backend.services import card_kit, notifier, lark_notifier

logger = logging.getLogger(__name__)


async def run_tail_decision_1440():
    if not is_workday():
        logger.info("[tail_decision] 非交易日, 跳过")
        return

    from backend.services.strength_quality_scanner import scan_strength_quality_snapshot
    from backend.services.sector_rotation_scanner import predict_sector_next_day
    from backend.services.weak_extreme_scanner import (
        collect_weak_extreme_hits, build_weak_extreme_section,
    )

    sq, pred, weak_hits = await asyncio.gather(
        scan_strength_quality_snapshot(return_only=True),
        predict_sector_next_day(return_only=True),
        collect_weak_extreme_hits(),
        return_exceptions=True,
    )
    if isinstance(sq, Exception):
        logger.warning(f"[tail_decision] 强势评分部分异常: {sq}")
        sq = None
    if isinstance(pred, Exception):
        logger.warning(f"[tail_decision] 次日预测部分异常: {pred}")
        pred = None
    if isinstance(weak_hits, Exception):
        logger.warning(f"[tail_decision] 弱势极限部分异常: {weak_hits}")
        weak_hits = None

    tlines: list[str] = ["【尾盘决策】"]
    elements: list = []
    have = False

    def _sep(title: str):
        elements.append(lark_notifier.md_element(f"<font color='grey'>━━━━━━ {title} ━━━━━━</font>"))
        tlines.append("")
        tlines.append(f"—— {title} ——")

    sq_meta: dict = (sq[2] if sq and len(sq) > 2 else {}) or {}
    pred_meta: dict = (pred[2] if pred and len(pred) > 2 else {}) or {}
    n_weak = len(weak_hits) if isinstance(weak_hits, list) else 0

    if sq:
        have = True
        _sep("真假强势评分")
        tlines += sq[0].split("\n")
        elements += sq[1]
    if pred:
        have = True
        _sep("次日板块预测")
        tlines += pred[0].split("\n")
        elements += pred[1]
    if weak_hits:
        try:
            section = build_weak_extreme_section(weak_hits)
        except Exception as e:
            logger.warning(f"[tail_decision] 弱势极限区块构建失败: {e}")
            section = ""
        if section:
            have = True
            _sep("弱势极限·尾盘候选")
            tlines.append(section)
            elements.append(lark_notifier.md_element(section))

    if not have:
        logger.info("[tail_decision] 三部分都无内容, 跳过")
        return
    # 摘要 = 事件 + 三部分各自最关键的一个数(基线 v1.1 信封标配)
    bits = []
    if sq:
        bits.append(f"真强势{sq_meta.get('real', 0)}只")
    if pred:
        bits.append(f"弱转强候选{pred_meta.get('wts', 0)}")
    if weak_hits:
        bits.append(f"弱势极限{n_weak}只")
    card = card_kit.Card(
        title="📊 尾盘决策", elements=elements, fallback="\n".join(tlines),
        family="intel", subtitle="真假强势 + 次日板块预测 + 弱势极限",
        summary=card_kit.summary_text("尾盘决策", *bits),
    )
    sent = await notifier.send_card(card)
    logger.info(f"[tail_decision] 尾盘决策合并卡推送={sent} "
                f"(强势={'✓' if sq else '✗'} 次日={'✓' if pred else '✗'} 弱势={'✓' if weak_hits else '✗'})")
