# -*- coding: utf-8 -*-
"""藏龙岛观点扫描 — 飞书群群主消息入库(不推送).

频率(自节流): 交易日 09:00-11:30 / 13:00-15:00 每 1 分钟, 其余(午休/盘后/周末) 3 分钟。
入口 scan_coach_posts() 注册为 interval/60s(见 database.py 种子), 内部按上述节流控制实际执行间隔。
去重靠 cfzy_biz_lark_coach_posts 唯一索引。不推送(用户本人飞书已收到原消息)。

token/授权失效兜底: 连续失败达阈值 → 登记进「系统健康·盘后汇总」提醒在服务器重新 lark-cli 授权。
"""
import logging
import re
import time
from datetime import datetime, time as dtime

from backend.core.config import load_config, DEFAULT_CONFIG
from backend.core.trading_calendar import is_workday
from backend.fetcher.lark_coach import (
    fetch_coach_messages, send_chat_text, send_chat_image_file,
    send_webhook_message, upload_relay_image,
    download_message_image, extract_image_key,
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
    """交易日 09:00-11:30 / 13:00-15:00 每 60s, 其余(午休/盘后/周末) 180s。
    观点采集不依赖行情开闸, 故不用 is_trading_time(那是 09:25 起), 自定义窗口从 09:00 起。"""
    now = datetime.now()
    if is_workday(now):
        t = now.time()
        if dtime(9, 0) <= t <= dtime(11, 30) or dtime(13, 0) <= t <= dtime(15, 0):
            return 60
    return 180


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


# ── 卡片形式转发(relay_style=card): 蓝头「藏龙岛观点 · 时间」+ 正文重点加粗 ──
# 与观点页同口径(LarkCoachView emphasize/splitMsg): 短标签(≤8字)+全角冒号视为重点加粗;
# -----分隔出的学员引用降为灰色小字(note)。
_LABEL_RE = re.compile(r"(^|[\s；;。！？!?、，,])([^\s，,。；;：:！？!?]{1,8})：")
_QUOTE_SPLIT_RE = re.compile(r"-{5,}\s")


def _emphasize_md(text: str) -> str:
    return _LABEL_RE.sub(lambda m: f"{m.group(1)}**{m.group(2)}：**", text)


def _build_relay_card(name: str, stamp: str, text: str | None = None,
                      img_key: str | None = None) -> dict:
    title = f"{name}观点 · {stamp}" if stamp else f"{name}观点"
    elements: list[dict] = []
    if text is not None:
        m = _QUOTE_SPLIT_RE.search(text)
        answer, quoted = (text[:m.start()].strip(), text[m.end():].strip()) if m else (text.strip(), "")
        if answer:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": _emphasize_md(answer)}})
        if quoted:
            elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": quoted}]})
        if not elements:   # 全空白兜底, 防发出无正文的空卡
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": text or " "}})
    if img_key:
        elements.append({"tag": "img", "img_key": img_key,
                         "alt": {"tag": "plain_text", "content": "观点图片"}})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
            "elements": elements}


async def _relay_pending(cfg: dict):
    """把 relayed_at 为空的消息按时序转发到用户的群(定稿走群自定义机器人 webhook)。

    形式由 relay_style 控制(后台「系统设置」可切):
      card(默认) = 蓝头卡片「藏龙岛观点 · MM-DD HH:MM」, 正文重点加粗, 图片原图嵌卡内;
      text       = 纯文本「【藏龙岛 MM-DD HH:MM】正文」。
    图片=默认档案下载→coachbot 应用上传拿 image_key→webhook 发卡/发图, 失败逐级降级
    (图片卡→独立图片消息→文本占位; 文字卡→文本)。配 relay_webhook 走 webhook 通道,
    没配则回退 lark-cli 直发(relay_chat_id+relay_send_as, 恒为文本/图片形式)。
    单条失败即停本轮(多为限流/网络, 换轮重试), 不整批标记防丢。
    """
    webhook = cfg.get("relay_webhook", "")
    chat_id = cfg.get("relay_chat_id", "")
    if not cfg.get("relay_enabled", False) or not (webhook or chat_id):
        return
    style = str(cfg.get("relay_style", "card")).lower()

    import asyncio as _asyncio
    from pathlib import Path

    media_dir = str(Path(__file__).resolve().parents[2] / "data" / "coach_media")

    async def _send_body(name: str, stamp: str, body: str):
        """文字内容按当前形式发; 卡片失败降级文本(再失败抛出, 由外层中止本轮)。"""
        if webhook:
            if style == "card":
                try:
                    await send_webhook_message(
                        webhook, {"msg_type": "interactive",
                                  "card": _build_relay_card(name, stamp, text=body)})
                    return
                except LarkCoachFetchError as e:
                    logger.warning(f"[lark_coach] 卡片发送失败, 降级文本: {e}")
            await send_webhook_message(
                webhook, {"msg_type": "text", "content": {"text": f"【{name} {stamp}】{body}"}})
        else:
            await send_chat_text(cfg, chat_id, f"【{name} {stamp}】{body}")

    rows = await repository.list_unrelayed_coach_posts(limit=40)
    sent = 0
    for r in rows:
        posted = r.get("posted_at")
        stamp = posted.strftime("%m-%d %H:%M") if posted else ""
        name = r.get("coach_name") or "藏龙岛"
        try:
            if r.get("msg_type") == "image" and (key := extract_image_key(r.get("content", ""))):
                try:
                    fname = f"{r['message_id']}.img"
                    if not (Path(media_dir) / fname).exists():
                        await download_message_image(cfg, r["message_id"], key, media_dir, fname)
                    if webhook:
                        img_key = await upload_relay_image(cfg, media_dir, fname)
                        sent_card = False
                        if style == "card":
                            try:
                                await send_webhook_message(
                                    webhook, {"msg_type": "interactive",
                                              "card": _build_relay_card(name, stamp, img_key=img_key)})
                                sent_card = True
                            except LarkCoachFetchError as e:
                                logger.warning(f"[lark_coach] 图片卡片失败, 降级独立图片({r['message_id']}): {e}")
                        if not sent_card:
                            await send_webhook_message(
                                webhook, {"msg_type": "image", "content": {"image_key": img_key}})
                    else:
                        await send_chat_image_file(cfg, chat_id, media_dir, fname)
                except LarkCoachFetchError as e:
                    logger.warning(f"[lark_coach] 图片转发降级为文本({r['message_id']}): {e}")
                    await _send_body(name, stamp, "[图片] 见「观潮」藏龙岛观点页或原群")
            else:
                await _send_body(name, stamp, r.get("content", ""))
        except LarkCoachFetchError as e:
            logger.warning(f"[lark_coach] 转发失败(已转{sent}条, 本轮中止换轮重试): {e}")
            return
        await repository.mark_coach_post_relayed(r["id"])
        sent += 1
        await _asyncio.sleep(0.3)   # 轻限速, 防触发飞书发送频控

    if sent:
        logger.info(f"[lark_coach] 本轮转发 {sent} 条到用户群")
