"""风暴聚合窗口 storm_aggregator (机制一, 基线第五节·聚合卡, v1.7.642)

同族个股信号在 90 秒窗口内凑够 ≥3 条 → 合并为一张聚合卡(card_kit.aggregate_card),
防普跌日 8 张卡轰炸; 窗口内 <3 条 → 到期后按原参数逐张发出(不合并不丢失)。

范围(硬边界):
  - 只聚 离场(exit) + 风险(risk) 族 —— 机会/情报/系统不聚(买点晚 90 秒可能误事, 且机会不成灾)
  - 大盘急跌(plunge)不进缓冲(它本来就是全局一张), 由 notifier 拦截点排除
  - 拦截点在 notifier.send_wechat_signal 全部闸门(生产IP/用户/推送偏好)之后 —— 进缓冲的
    都是"本来就该发出去"的卡, 聚合器只决定"合并发"还是"逐张发", 不再做任何过滤

设计(学 alert_throttle 的 锁/缓冲/_requeue 模式):
  - 每族一个窗口缓冲; 锁内只做状态判定/取数据, 网络 IO 在锁外
  - 首条进缓冲时 loop.call_later 挂一次性到期结算; 结算带窗口序号 seq 保证幂等
    (定时器与周期兜底 flush 重复触发时, 后到者见 seq 不匹配/缓冲已空则直接返回)
  - 周期兜底: flush_expired() 搭既有 alert_throttle_flush 60s 任务(task_registry),
    覆盖"定时器丢失/入队时无事件循环"的场景
  - 发送失败 _requeue 放回缓冲队首开新窗口, 下轮重试, 不静默丢件; 超过重试上限才丢弃并记错误日志
  - 缓冲期间原卡全部参数留存在 item["params"], 逐发时全量回放 notifier._send_wechat_signal_direct

配置(config.json, load_config 读, 缺省即生效):
  - storm_aggregate_enabled: 总开关, 默认 True; 关闭时 intercept 直接放行(直通原路径)
  - storm_aggregate_window: 窗口秒数, 默认 90
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

AGGREGATE_MIN = 3                       # 窗口内凑够几条才合并聚合卡
DEFAULT_WINDOW_SECONDS = 90.0
AGGREGATE_FAMILIES = ("exit", "risk")   # 只聚离场+风险族
MAX_RETRIES = 3                         # 单件发送失败重排上限(防渠道长瘫时无限循环)

# 归因阈值: 上证跌幅达到该值视为"大盘在跌"; 同行业家数占比过半(且≥2)视为"板块共振"
_INDEX_DROP_PCT = -0.8


@dataclass
class _Window:
    items: list[dict] = field(default_factory=list)
    seq: int = 0                        # 窗口序号: 每开新窗 +1, 结算幂等靠它
    opened_at: datetime | None = None   # 窗口起点(聚合卡副标题「HH:MM~HH:MM 合并推送」用)
    due_at: float = 0.0                 # time.monotonic 截止, 周期兜底 flush 判到期用


_windows: dict[str, _Window] = defaultdict(_Window)
_lock = asyncio.Lock()


def _load_settings() -> tuple[bool, float]:
    """(开关, 窗口秒数)。读 config 失败按默认开+90s, 不因配置问题弄丢推送。"""
    try:
        from backend.core.config import load_config
        cfg = load_config()
        enabled = bool(cfg.get("storm_aggregate_enabled", True))
        window = float(cfg.get("storm_aggregate_window", DEFAULT_WINDOW_SECONDS) or 0)
    except Exception:
        return True, DEFAULT_WINDOW_SECONDS
    if window <= 0:
        window = DEFAULT_WINDOW_SECONDS
    return enabled, window


async def intercept(family: str, params: dict, send=None) -> bool:
    """闸后拦截入口: 把一条已通过全部闸门的个股信号放进族缓冲。

    params = send_wechat_signal 的全量参数 dict(原样留存, 逐发时不丢字段);
    send = 可选 async 零参回调(测试注入用), 缺省逐发走 notifier._send_wechat_signal_direct(**params)。
    返回 True=聚合器接管(调用方直接返回, 由窗口结算负责发出);
    返回 False=不聚合(开关关/非聚合族), 调用方走原路径直发。
    """
    enabled, window = _load_settings()
    if not enabled or family not in AGGREGATE_FAMILIES:
        return False
    item = {"params": dict(params), "send": send, "retries": 0}
    async with _lock:
        win = _windows[family]
        first = not win.items
        if first:
            win.seq += 1
            win.opened_at = datetime.now()
            win.due_at = time.monotonic() + window
        win.items.append(item)
        seq = win.seq
        pending = len(win.items)
    if first:
        _schedule(family, seq, window)
    logger.info(f"[storm] {family} 缓冲+1 -> {pending} 条 "
                f"({params.get('name')}/{params.get('signal_name')}), 窗口 seq={seq}")
    return True


def _schedule(family: str, seq: int, delay: float) -> None:
    """挂一次性到期结算定时器; 无运行中事件循环(同步上下文)则靠周期 flush_expired 兜底。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(f"[storm] {family} 无事件循环, 窗口 seq={seq} 交给周期 flush 兜底")
        return
    loop.call_later(delay, lambda: asyncio.ensure_future(settle(family, seq)))


async def settle(family: str, seq: int) -> None:
    """窗口到期结算(幂等: seq 不匹配/缓冲空 = 已被结算或已开新窗, 直接返回)。
    ≥AGGREGATE_MIN 条 → 合并一张聚合卡; <AGGREGATE_MIN 条 → 按原参数逐张发出。
    发送失败 → _requeue 放回缓冲下轮重试(学 alert_throttle, 不静默丢件)。"""
    async with _lock:
        win = _windows.get(family)
        if win is None or win.seq != seq or not win.items:
            return                       # 幂等出口: 定时器/周期flush谁先到谁结算, 后到无事可做
        items = list(win.items)
        opened_at = win.opened_at
        win.items.clear()
        win.opened_at = None
    window_label = f"{opened_at:%H:%M}~{datetime.now():%H:%M}" if opened_at else ""

    # 全部带 mute_lark(只静音飞书)时不聚合: 聚合卡通道(send_card)没有 mute_lark 语义,
    # 逐发路径能正确尊重静音位 —— 宁可多几张(反正飞书静音), 不违背静音语义。
    # (「今日免打扰」用户偏好源头已拆除 2026-07, 正常链路 params 不再携带 mute_lark;
    #  本检查保留作通道控制位兜底, 与 _send_wechat_signal_direct 的形参口径一致。)
    all_muted = all(it["params"].get("mute_lark") for it in items)

    if len(items) >= AGGREGATE_MIN and not all_muted:
        if await _send_aggregate(family, items, window_label):
            logger.info(f"[storm] {family} 聚合卡已发出(合并 {len(items)} 条, 窗口 {window_label})")
            return
        logger.warning(f"[storm] {family} 聚合卡发送失败, {len(items)} 条重排")
        await _requeue(family, items)
        return

    failed: list[dict] = []
    for it in items:
        if not await _send_single(it):
            failed.append(it)
    if failed:
        await _requeue(family, failed)


async def flush_expired() -> None:
    """周期兜底(搭 task_registry 的 alert_throttle_flush 60s 任务):
    结算所有已到期窗口, 覆盖定时器丢失/入队时无事件循环的场景。未到期窗口不动。"""
    now = time.monotonic()
    due: list[tuple[str, int]] = []
    async with _lock:
        for family, win in _windows.items():
            if win.items and now >= win.due_at:
                due.append((family, win.seq))
    for family, seq in due:
        await settle(family, seq)


async def _requeue(family: str, items: list[dict]) -> None:
    """发送失败不丢件(学 alert_throttle._requeue): 放回缓冲队首并开新窗口, 下轮重试;
    单件超过 MAX_RETRIES 次仍失败才丢弃(记 error 日志), 防渠道长瘫时无限空转。"""
    kept: list[dict] = []
    for it in items:
        it["retries"] = it.get("retries", 0) + 1
        if it["retries"] > MAX_RETRIES:
            p = it.get("params", {})
            logger.error(f"[storm] {family} 重试超限({MAX_RETRIES})丢弃: "
                         f"{p.get('name')}({p.get('code')}) {p.get('signal_name')}")
        else:
            kept.append(it)
    if not kept:
        return
    _, window = _load_settings()
    async with _lock:
        win = _windows[family]
        first = not win.items
        win.items[:0] = kept            # 放回队首, 保持时间顺序
        if first:
            win.seq += 1
            win.opened_at = datetime.now()
            win.due_at = time.monotonic() + window
        seq = win.seq
    if first:
        _schedule(family, seq, window)
    logger.warning(f"[storm] {family}: {len(kept)} 条推送失败已放回缓冲, 下轮重试(seq={seq})")


async def _send_single(item: dict) -> bool:
    """按缓冲留存的原参数原样发出一张卡(不合并不丢字段)。返回 True=至少一渠道成功。"""
    p = item.get("params", {})
    try:
        send = item.get("send")
        if send is not None:            # 测试注入的回调
            return bool(await send())
        from backend.services import notifier
        return bool(await notifier._send_wechat_signal_direct(**p))
    except Exception as e:
        logger.warning(f"[storm] 逐发异常({p.get('name')} {p.get('signal_name')}): {e}")
        return False


# ── 聚合卡构造与归因 ──

def _short_signal(name: str) -> str:
    """短信号名(md_table 全短列铁律): 「模型名·动作」取动作段(止损/清剩半/止盈减半…),
    无「·」则截前 8 字。"""
    s = (name or "").strip()
    if "·" in s:
        tail = s.split("·")[-1].strip()
        if tail:
            s = tail
    return s[:8]


async def _build_cause(items: list[dict]) -> tuple[str, str]:
    """(归因md, 短标签)。尽力归因: 大盘在跌(读 market_overview 快照表, 不打外部接口) +
    多数票同板块(读 cfzy_biz_stock_pool.industry)。拿不到就写「N 只持仓/自选同窗口触发」。"""
    n = len(items)
    parts: list[str] = []
    tag = ""
    try:                                # 大盘: 上证跌幅
        from backend.models import repository
        ov = await repository.get_market_overview()
        for idx in (ov or {}).get("a_indices") or []:
            if "上证" in str(idx.get("name", "")):
                pct = float(idx.get("pct") or 0)
                if pct <= _INDEX_DROP_PCT:
                    parts.append(f"大盘急跌 <font color='green'>**{pct:+.1f}%**</font>")
                    tag = tag or "大盘急跌"
                break
    except Exception as e:
        logger.debug(f"[storm] 大盘归因失败: {e}")
    try:                                # 板块: 多数票同行业 → 板块共振
        codes = [it["params"].get("code") for it in items if it["params"].get("code")]
        uid = next((it["params"].get("user_id") for it in items if it["params"].get("user_id")), 1)
        if len(codes) >= 2:
            from backend.models.repo._db import _fetchall
            marks = ",".join(["%s"] * len(codes))
            rows = await _fetchall(
                f"SELECT industry, COUNT(*) AS cnt FROM cfzy_biz_stock_pool "
                f"WHERE user_id=%s AND code IN ({marks}) AND industry IS NOT NULL AND industry<>'' "
                f"GROUP BY industry ORDER BY cnt DESC LIMIT 1",
                (uid, *codes))
            if rows:
                ind, cnt = rows[0]["industry"], int(rows[0]["cnt"])
                if cnt >= 2 and cnt * 2 >= n:
                    parts.append(f"{ind}板块共振下杀（{cnt}/{n} 只同板块）")
                    tag = "板块共振"
    except Exception as e:
        logger.debug(f"[storm] 板块归因失败: {e}")
    if not parts:
        return f"{n} 只持仓/自选同窗口触发", ""
    return "，".join(parts), tag


async def _send_aggregate(family: str, items: list[dict], window_label: str) -> bool:
    """把窗口内 ≥3 条信号合并为一张聚合卡发出。返回 True=至少一渠道成功。"""
    try:
        from backend.services import card_kit, notifier
        rows = []
        fold_lines = []
        for it in items:
            p = it["params"]
            pct = float(p.get("pct_change") or 0)
            rows.append((p.get("name") or p.get("code") or "?",
                         card_kit.pct_md(pct), _short_signal(p.get("signal_name", ""))))
            price = float(p.get("price") or 0)
            fold_lines.append(f"{p.get('name')}({p.get('code')}) {p.get('signal_name')} "
                              f"¥{price:.2f}（{pct:+.1f}%）")
        cause_md, tag = await _build_cause(items)
        advice_text = ("共振下杀，禁抄底，先降仓等企稳" if tag
                       else "多票同窗触发离场，逐只核对持仓")
        card = card_kit.aggregate_card(
            "离场信号" if family == "exit" else "风险信号", rows,
            cause_md=cause_md, advice_text=advice_text,
            window=window_label, tag=tag, family="risk",   # 风暴聚合统一 orange 风险色(基线第五节)
            table_headers=["股票", "涨跌", "信号"],
            fold_summary="触发明细（现价/完整信号名）",
            fold_detail="\n".join(fold_lines))
        return bool(await notifier.send_card(card))
    except Exception as e:
        logger.warning(f"[storm] 聚合卡构造/发送异常: {e}")
        return False


def get_buffer_stats() -> dict[str, dict]:
    """诊断用: 各族窗口的缓冲条数/窗口序号/起点。"""
    return {
        family: {
            "pending": len(win.items),
            "seq": win.seq,
            "opened_at": win.opened_at.strftime("%H:%M:%S") if win.opened_at else None,
        }
        for family, win in _windows.items()
    }
