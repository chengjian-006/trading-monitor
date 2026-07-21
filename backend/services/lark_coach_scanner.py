# -*- coding: utf-8 -*-
"""藏龙岛观点扫描 — 飞书群群主消息入库(不推送).

频率(自节流): 交易时段 1 分钟, 其余(盘后/周末) 10 分钟。
入口 scan_coach_posts() 注册为 interval/60s(见 database.py 种子), 内部按上述节流控制实际执行间隔。
去重靠 cfzy_biz_lark_coach_posts 唯一索引。不推送(用户本人飞书已收到原消息)。

token/授权失效兜底: 连续失败达阈值 → 登记进「系统健康·盘后汇总」提醒在服务器重新 lark-cli 授权。
"""
import logging
import time

from backend.core.config import load_config
from backend.core.trading_calendar import is_trading_time
from backend.fetcher.lark_coach import fetch_coach_messages, LarkCoachFetchError
from backend.models import repository

logger = logging.getLogger(__name__)

_last_scan: float = 0.0

# 授权失效兜底: 连续失败达阈值即登记告警, 恢复后销号
_fail_count: int = 0
_fail_alerted: bool = False
FAIL_THRESHOLD = 3


def _interval_seconds() -> int:
    """交易时段 60s, 其余 600s(盘后 10 分钟)。"""
    return 60 if is_trading_time() else 600


async def scan_coach_posts():
    cfg = load_config().get("lark_coach_tracking", {})
    if not cfg.get("enabled", False):
        return

    global _last_scan, _fail_count, _fail_alerted
    interval = _interval_seconds()
    now = time.monotonic()
    if now - _last_scan < interval:
        return
    _last_scan = now

    try:
        messages = await fetch_coach_messages(cfg)
    except LarkCoachFetchError as e:
        _fail_count += 1
        logger.warning(f"[lark_coach] 拉取失败(连续 {_fail_count} 次): {e}")
        if _fail_count >= FAIL_THRESHOLD and not _fail_alerted:
            _fail_alerted = True
            from backend.services.system_health import report_issue
            name = cfg.get("coach_name", "藏龙岛")
            report_issue("藏龙岛观点", f"「{name}」连续{_fail_count}次拉取失败: {e}"
                                       f"(多为服务器 lark-cli 授权过期, 需在服务器重新 lark-cli auth login)")
        return

    # 拉取成功: 若此前告过警补一条恢复登记
    if _fail_alerted:
        from backend.services.system_health import report_issue
        report_issue("藏龙岛观点", f"「{cfg.get('coach_name', '藏龙岛')}」拉取已恢复")
    _fail_count = 0
    _fail_alerted = False

    if not messages:
        return

    # 按发布时间正序入库(老的先写), 保证 id 递增与时序一致
    ordered = sorted(messages, key=lambda m: (m.get("posted_at") is None, m.get("posted_at")))
    total_new = 0
    for m in ordered:
        is_new = await repository.save_coach_post(
            message_id=m["message_id"], chat_id=m["chat_id"],
            sender_open_id=m["sender_open_id"], coach_name=m["coach_name"],
            posted_at=m["posted_at"], content=m["content"], msg_type=m["msg_type"],
        )
        if is_new:
            total_new += 1

    if total_new:
        logger.info(f"[lark_coach] 本轮新消息 {total_new} 条")
