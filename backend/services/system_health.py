# -*- coding: utf-8 -*-
"""系统健康·盘后汇总 (v1.7.557 批次E): 把各类"系统故障"告警(数据源交叉校验偏差 / 博主
拉取中断等)累积起来, 每日盘后合成一条汇总推送, 不再实时逐类刷屏。当日无异常则不推。

进程内累积(生产单 worker, 重启丢当日已积, best-effort); 更紧急的实时类(行情源健康
data_health)不并入, 仍即时告警。急跌/风控等交易类不在此列。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# {date: [(source, message, hhmm)]}
_issues: dict[str, list[tuple[str, str, str]]] = {}


def report_issue(source: str, message: str) -> None:
    """登记一条系统故障(不立即推送, 待盘后汇总)。source=类别名, message=简述。"""
    today = datetime.now().strftime("%Y-%m-%d")
    for d in list(_issues):          # 跨日清理
        if d != today:
            del _issues[d]
    _issues.setdefault(today, []).append(
        (source, (message or "").strip(), datetime.now().strftime("%H:%M")))
    logger.info(f"[system_health] 记录系统故障[{source}] {(message or '')[:50]}")


def build_digest_card(items: list[tuple[str, str, str]]):
    """系统健康盘后汇总 → 基线 v1.1 系统卡(grey): 结论行灯串 → 异常清单(>8条 Top5+全量折叠) → 👉建议。
    items = [(source, message, hhmm), ...]"""
    from backend.services import card_kit
    from backend.services.lark_notifier import md_element

    sources = list(dict.fromkeys(s for s, _, _ in items))
    lights = card_kit.light_string([("warn", s) for s in sources])
    elements: list = [md_element(
        f"{lights}\n今日系统故障 **{len(items)}** 项，涉及 {len(sources)} 类：" + "、".join(sources))]

    lines = [f"- [{hhmm}] **{source}**: {(msg.split(chr(10))[0] if msg else '')[:90]}"
             for source, msg, hhmm in items]
    fold_detail = ""
    if len(lines) > 8:
        elements.append(md_element("\n".join(lines[:5]) + f"\n…等 **{len(lines)}** 项，全量见折叠"))
        fold_detail = "\n".join(lines)
    else:
        elements.append(md_element("\n".join(lines)))
    elements.append(card_kit.advice("按需排查，完整明细见后端日志"))
    if fold_detail:
        elements.append(card_kit.fold(f"全部故障（{len(lines)}项）", fold_detail))
    elements.append(card_kit.fold(
        "汇总口径", "各类系统故障当日合并、盘后一次汇总(不实时刷屏); 更紧急的行情源健康预警仍即时推送。"))

    fb_lines = [f"🩺 系统健康·盘后汇总（{len(items)} 项）", ""]
    fb_lines += [f"• [{hhmm}] {source}: {(msg.split(chr(10))[0] if msg else '')[:90]}"
                 for source, msg, hhmm in items]
    fb_lines += ["", "（各类系统故障当日合并汇总, 完整明细见后端日志）"]
    return card_kit.Card(
        title=f"⚙️ 系统健康盘后汇总 · {len(items)}项",
        elements=elements, fallback="\n".join(fb_lines), family="system",
        summary=card_kit.summary_text("系统健康盘后汇总", f"{len(items)}项故障",
                                      sources[0] if len(sources) == 1 else f"{len(sources)}类"))


async def run_system_health_digest() -> None:
    """盘后系统健康汇总: 当日有故障才推一张系统灰卡, 无则跳过。"""
    today = datetime.now().strftime("%Y-%m-%d")
    items = _issues.get(today) or []
    if not items:
        logger.info("[system_health] 今日无系统故障, 不推汇总")
        return
    try:
        from backend.services import notifier
        await notifier.send_card(build_digest_card(items))
        logger.info(f"[system_health] 已推盘后汇总, {len(items)} 项")
    except Exception as e:
        logger.warning(f"[system_health] 推送失败: {e}")
    finally:
        _issues.pop(today, None)
