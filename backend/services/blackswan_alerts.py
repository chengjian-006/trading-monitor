# -*- coding: utf-8 -*-
"""自选股黑天鹅预警 — 合并推送编排 (v1.7.x).

把原先两条独立推送(风险公告 18:00 + 财务红旗 18:30)合并成**一张卡、两个区域**:
  🚨 风险公告(监管/财务硬信号)  +  📉 财务红旗(年报指标打分)

每日 18:30 一次。两个采集器各自独立扫描+落库去重(状态仍各用自己的表), 本编排只负责
把两边「本次新增」的命中拼成一张卡发出去。两个区域常驻: 某类本次无新增则该区标
「本次无新增」(保持结构), ≥1 个区域有新增才发卡(都空则静默, 与合并前一致)。
"""
import asyncio
import logging

from backend.services import financial_risk_scanner as fin
from backend.services import risk_announcement_scanner as ann

logger = logging.getLogger(__name__)


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _build_combined(ann_hits: list[dict], fin_hits: list[dict],
                    ann_verdicts: dict | None = None) -> tuple[str, list]:
    """合成 (微信/PushPlus 文本, 飞书表格 elements)。两区域常驻。
    ann_verdicts={code:{emoji,severity,text}} 给定时, 风险公告区每只票挂一句 AI 研判。"""
    no_new = "本次无新增"

    # —— 微信/PushPlus 文本版: 两区域 ——
    text_parts = [
        f"🚨 风险公告（{len(ann_hits)}）",
        ann.ann_section_text(ann_hits, ann_verdicts) if ann_hits else no_new,
        f"\n📉 财务红旗（{len(fin_hits)}）",
        fin.fin_section_text(fin_hits) if fin_hits else no_new,
        "\n纯提示, 不影响买卖点。",
    ]
    text = "\n".join(text_parts)

    # —— 飞书表格版: 两区域 ——
    elements = [_md(f"**🚨 风险公告（{len(ann_hits)}）** 监管/财务硬信号")]
    elements.append(ann.ann_table(ann_hits, ann_verdicts) if ann_hits else _md(no_new))
    elements.append(_md(f"**📉 财务红旗（{len(fin_hits)}）** 年报指标打分"))
    elements.append(fin.fin_table(fin_hits) if fin_hits else _md(no_new))
    elements.append(_md("**纯提示, 不影响买卖点。** 监管/财务红旗多为 ST 黑天鹅前兆。"))
    return text, elements


async def scan_blackswan_alerts():
    """入口(cron 18:30): 并发跑两个采集器, 合并成一张两区域卡推送。"""
    ann_res, fin_res = await asyncio.gather(
        ann.collect_risk_ann_hits(),
        fin.collect_financial_risk_hits(),
        return_exceptions=True,
    )
    ann_hits = ann_res if isinstance(ann_res, list) else []
    fin_hits = fin_res if isinstance(fin_res, list) else []
    if isinstance(ann_res, Exception):
        logger.warning(f"[blackswan] 风险公告采集失败: {ann_res}")
    if isinstance(fin_res, Exception):
        logger.warning(f"[blackswan] 财务红旗采集失败: {fin_res}")

    if not ann_hits and not fin_hits:
        logger.info("[blackswan] 风险公告/财务红旗均无新增, 不推送")
        return

    # 风险公告逐股 AI 研判(第三层兜底: 整体失败也不卡推送, 退回无研判的原卡)
    ann_verdicts: dict = {}
    if ann_hits:
        try:
            from backend.services import blackswan_ai
            ann_verdicts = await blackswan_ai.generate_risk_verdicts(ann_hits)
        except Exception as e:
            logger.warning(f"[blackswan] AI 研判整体失败, 退回无研判卡: {e}")

    from backend.services import notifier
    text, elements = _build_combined(ann_hits, fin_hits, ann_verdicts)
    ok = await notifier.send_dual_card(text, lark_title="⚠️ 自选股黑天鹅预警", elements=elements)
    logger.warning(f"[blackswan] 公告{len(ann_hits)}条(AI研判{len(ann_verdicts)})+财务红旗{len(fin_hits)}只, "
                   f"推送={'成功' if ok else '失败/跳过'}")
