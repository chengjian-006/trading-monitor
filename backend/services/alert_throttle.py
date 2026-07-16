"""通用提醒节流+合并模块 (v1.7.x)

为避免「同类提醒短时间内频繁打扰」(资金回流·板块预警、卖点撤销 等):
  - 每个 alert_type 维护独立缓冲 + last_push_at
  - enqueue() 把消息放进缓冲, 若距上次推送 >= throttle 秒则立即 flush 合并发送
  - flush_all() 由调度任务周期调用, 兜底把已到期的缓冲发出去

设计要点:
  - merger(items: list[dict]) -> str: 把缓冲里的多条消息合并为一条文本
  - 首次推送(last_push_at 为 None)立即发出, 不延迟
  - 同 alert_type 用同一缓冲, 不同 alert_type 之间互不影响
  - 锁内只做状态判定/取数据, 网络 IO 在锁外做
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)

THROTTLE_DEFAULT_SECONDS = 15 * 60


@dataclass
class _Buffer:
    items: list[dict] = field(default_factory=list)
    last_push_at: datetime | None = None


_buffers: dict[str, _Buffer] = defaultdict(_Buffer)
_mergers: dict[str, Callable[[list[dict]], str]] = {}
# lark_card_builder: (items) -> (title, elements) | None, 有则飞书发原生表格卡(企微仍发文本)
_lark_card_builders: dict[str, Callable] = {}
_throttles: dict[str, int] = {}
_lock = asyncio.Lock()


def register(alert_type: str, merger: Callable[[list[dict]], str],
             throttle_seconds: int = THROTTLE_DEFAULT_SECONDS,
             lark_card_builder: Callable | None = None) -> None:
    """注册某 alert_type 的合并函数 / 节流间隔 / 可选飞书表格卡构造器。
    lark_card_builder 是 (items) -> (title, elements) | None: 返回非空则飞书发原生表格卡(企微始终发文本兜底)。
    """
    _mergers[alert_type] = merger
    _throttles[alert_type] = throttle_seconds
    if lark_card_builder is not None:
        _lark_card_builders[alert_type] = lark_card_builder


def _take_if_due(alert_type: str) -> tuple[str, list[dict]] | None:
    """需在 _lock 内调用: 缓冲若已到节流期, 取走并重置 last_push_at, 返回 (alert_type, items)。"""
    buf = _buffers[alert_type]
    if not buf.items:
        return None
    throttle = _throttles.get(alert_type, THROTTLE_DEFAULT_SECONDS)
    now = datetime.now()
    if buf.last_push_at is not None and (now - buf.last_push_at).total_seconds() < throttle:
        return None
    items = list(buf.items)
    buf.items.clear()
    buf.last_push_at = now
    return alert_type, items


async def _requeue(alert_type: str, items: list[dict]) -> None:
    """v1.7.569: 推送全渠道失败 → 把取走的 items 放回缓冲队首, 并回拨 last_push_at 使其立即到期,
    下一轮 enqueue/flush_all 重试, 不再静默丢件。仅在"一条都没发出去"时调用(部分成功不重排, 免重复)。"""
    async with _lock:
        buf = _buffers[alert_type]
        buf.items[:0] = items          # 放回队首, 保持时间顺序
        buf.last_push_at = None         # 立即到期
    logger.warning(f"[alert_throttle] {alert_type}: {len(items)} 条推送失败已放回缓冲, 下轮重试")


async def _send(alert_type: str, items: list[dict]) -> bool:
    """返回 True=已交付(至少一个渠道成功)或无需重试(无merger/空文本/非生产); False=全渠道失败需重排。"""
    merger = _mergers.get(alert_type)
    if not merger:
        logger.warning(f"[alert_throttle] {alert_type} 缺少 merger, 丢弃 {len(items)} 条")
        return True                    # 永久性问题, 重排也没用
    try:
        text = merger(items)
    except Exception as e:
        logger.warning(f"[alert_throttle] {alert_type} merger 抛错, 丢弃: {e}")
        return True                    # 重排会再次抛错, 不重试
    if not text:
        return True
    try:
        from backend.core.config import is_production
        if not await is_production():
            logger.info(f"[alert_throttle] 非生产环境, 跳过 flush {alert_type}({len(items)}条)")
            return True                # 有意跳过, 非失败, 不重排
        from backend.services import notifier
        # 注册了飞书表格卡构造器 → 飞书发原生表格卡(企微仍文本); 否则走纯文本双通道
        card_builder = _lark_card_builders.get(alert_type)
        ok = False
        sent_via_card = False
        if card_builder:
            try:
                built = card_builder(items)
            except Exception as e:
                logger.warning(f"[alert_throttle] {alert_type} 飞书表格卡构造失败, 回退文本: {e}")
                built = None
            if built:
                # 兼容两种返回: (title, elements) 或 (title, elements, extra)
                # extra = 信封字段 dict(template/summary/subtitle/text_tags, 基线v1.1)
                if len(built) == 3:
                    title, elements, extra = built
                else:
                    title, elements = built
                    extra = {}
                ok = await notifier.send_dual_card(text, lark_title=title, elements=elements,
                                                   **(extra or {}))
                sent_via_card = True
        if not sent_via_card:
            ok = await notifier.send_wechat_text(text)
        if ok:
            logger.info(f"[alert_throttle] flush {alert_type}: 合并 {len(items)} 条已推送")
        else:
            logger.warning(f"[alert_throttle] flush {alert_type}: {len(items)} 条全渠道未成功")
        return bool(ok)
    except Exception as e:
        logger.warning(f"[alert_throttle] {alert_type} 推送异常: {e}")
        return False


async def enqueue(alert_type: str, payload: dict) -> None:
    """加入缓冲。若距上次推送已超节流间隔(或首次), 立即 flush。"""
    async with _lock:
        _buffers[alert_type].items.append(payload)
        pending = _take_if_due(alert_type)
    if pending:
        if not await _send(*pending):
            await _requeue(*pending)


async def flush_all() -> None:
    """周期任务调用: 把所有到期的 alert_type 缓冲一次性 flush 出去。"""
    pending_list: list[tuple[str, list[dict]]] = []
    async with _lock:
        for alert_type in list(_buffers.keys()):
            p = _take_if_due(alert_type)
            if p:
                pending_list.append(p)
    for p in pending_list:
        if not await _send(*p):
            await _requeue(*p)


def get_buffer_stats() -> dict[str, dict]:
    """诊断用: 返回各 alert_type 的缓冲条数和上次推送时间。"""
    return {
        at: {
            "pending": len(buf.items),
            "last_push_at": buf.last_push_at.strftime("%Y-%m-%d %H:%M:%S") if buf.last_push_at else None,
            "throttle_seconds": _throttles.get(at, THROTTLE_DEFAULT_SECONDS),
        }
        for at, buf in _buffers.items()
    }
