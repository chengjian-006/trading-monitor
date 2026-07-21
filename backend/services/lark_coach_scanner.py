# -*- coding: utf-8 -*-
"""藏龙岛观点扫描 — 飞书群群主消息入库(不推送).

频率(自节流): 交易时段 1 分钟, 其余(盘后/周末) 10 分钟。
入口 scan_coach_posts() 注册为 interval/60s(见 database.py 种子), 内部按上述节流控制实际执行间隔。
去重靠 cfzy_biz_lark_coach_posts 唯一索引。不推送(用户本人飞书已收到原消息)。

token/授权失效兜底: 连续失败达阈值 → 登记进「系统健康·盘后汇总」提醒在服务器重新 lark-cli 授权。
"""
import logging
import time

from backend.core.config import load_config, DEFAULT_CONFIG
from backend.core.trading_calendar import is_trading_time
from backend.fetcher.lark_coach import (
    fetch_coach_messages, send_chat_text, send_chat_image, extract_image_key,
    LarkCoachFetchError,
)
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


def _load_cfg() -> dict:
    """lark_coach_tracking 段: load_config 顶层整段覆盖, 服务器 config.json 缺新键时
    (如 relay_*)用代码默认段逐键补齐, 免得每加键都要动生产配置。"""
    return {**DEFAULT_CONFIG.get("lark_coach_tracking", {}),
            **load_config().get("lark_coach_tracking", {})}


async def scan_coach_posts():
    cfg = _load_cfg()
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

    # 入库后把未转发的消息补转到用户自建群(含历史回填; 失败不标记, 下轮自动重试)
    await _relay_pending(cfg)


async def _relay_pending(cfg: dict):
    """把 relayed_at 为空的消息按时序转发到 relay_chat_id 群(user 身份)。

    文本按「【藏龙岛 MM-DD HH:MM】正文」发; 图片按原 image_key 重发。
    单条失败即停本轮(多为权限/限流, 换轮重试), 不整批标记防丢。
    """
    chat_id = cfg.get("relay_chat_id", "")
    if not cfg.get("relay_enabled", False) or not chat_id:
        return

    import asyncio as _asyncio

    rows = await repository.list_unrelayed_coach_posts(limit=40)
    sent = 0
    for r in rows:
        posted = r.get("posted_at")
        stamp = posted.strftime("%m-%d %H:%M") if posted else ""
        name = r.get("coach_name") or "藏龙岛"
        try:
            if r.get("msg_type") == "image" and (key := extract_image_key(r.get("content", ""))):
                await send_chat_image(cfg, chat_id, key)
            else:
                await send_chat_text(cfg, chat_id, f"【{name} {stamp}】{r.get('content', '')}")
        except LarkCoachFetchError as e:
            logger.warning(f"[lark_coach] 转发失败(已转{sent}条, 本轮中止换轮重试): {e}")
            return
        await repository.mark_coach_post_relayed(r["id"])
        sent += 1
        await _asyncio.sleep(0.3)   # 轻限速, 防触发飞书发送频控

    if sent:
        logger.info(f"[lark_coach] 本轮转发 {sent} 条到自建群")
