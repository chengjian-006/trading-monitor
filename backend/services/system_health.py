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


async def run_system_health_digest() -> None:
    """盘后系统健康汇总: 当日有故障才推一条, 无则跳过。"""
    today = datetime.now().strftime("%Y-%m-%d")
    items = _issues.get(today) or []
    if not items:
        logger.info("[system_health] 今日无系统故障, 不推汇总")
        return
    lines = [f"🩺 系统健康·盘后汇总（{len(items)} 项）", ""]
    for source, msg, hhmm in items:
        first = (msg.split("\n")[0] if msg else "")[:90]
        lines.append(f"• [{hhmm}] {source}: {first}")
    lines.append("")
    lines.append("（各类系统故障当日合并汇总, 完整明细见后端日志）")
    text = "\n".join(lines)
    try:
        from backend.services import notifier
        await notifier.send_dual(text, lark_title="🩺 系统健康·盘后汇总", template="orange")
        logger.info(f"[system_health] 已推盘后汇总, {len(items)} 项")
    except Exception as e:
        logger.warning(f"[system_health] 推送失败: {e}")
    finally:
        _issues.pop(today, None)
