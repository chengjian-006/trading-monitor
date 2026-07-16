"""大盘退潮风控 — 全市场涨停家数较前日骤降≥40% → 对持仓强势股推"退潮减仓"提示。

来源: 三模型回测(2026-06-04, [[project_3model-exploration-0604]])。其中"大盘退潮"是唯一验证
有效的市场级风控: 只在 ~10% 的"整板抽血"日触发, 退潮日离场显著压回撤、资金周转翻倍(PF1.53)。

口径: 今日涨停家数 ≤ 昨日涨停家数 × 0.6(即 ↓≥40%)。
  - 仅盘中, 且 ≥11:00(上午涨停已充分封板, 避免小基数失真)。
  - 昨日涨停 < 20 家不判(基数太小)。
  - 每个交易日最多推一次。
"""
import logging
from datetime import datetime

from backend.core.trading_calendar import is_trading_time
from backend.models import repository

logger = logging.getLogger(__name__)

DROP_RATIO = 0.6      # 今日 ≤ 昨日×0.6 → 骤降≥40%
MIN_PREV_LU = 20      # 昨日涨停太少不判
# 去重用 DB(防进程重启重推): 市场级信号, 哨兵 code 同 plunge("000001"), signal_id 归 PLUNGE 退潮族。
_EBB_CODE = "000001"
_EBB_SIGNAL_ID = "PLUNGE_MARKET_EBB"
_EBB_USER_ID = 1


async def detect_market_ebb():
    if not is_trading_time():
        return
    now = datetime.now()
    if now.hour < 11:            # 11:00 前涨停未充分封板, 不判
        return
    today = now.strftime("%Y-%m-%d")

    cur = await repository.get_latest_emotion()
    if not cur or str(cur.get("trade_date") or "")[:10] != today:
        return
    today_lu = cur.get("limit_up_count")
    prev = await repository.get_last_emotion_before(today)
    prev_lu = prev.get("limit_up_count") if prev else None
    if not today_lu or not prev_lu or prev_lu < MIN_PREV_LU:
        return
    if today_lu / prev_lu > DROP_RATIO:
        return

    # DB 去重(主权威, 防进程重启丢内存状态后重推): 当日已发过就跳过。查询失败保守跳过本轮。
    try:
        if await repository.signal_already_sent_today(_EBB_CODE, _EBB_SIGNAL_ID, _EBB_USER_ID):
            return
    except Exception as e:
        logger.error(f"[market_ebb] DB 去重查询失败, 本轮跳过: {e}")
        return

    drop_pct = round((1 - today_lu / prev_lu) * 100)
    try:
        holds = [s for s in (await repository.list_all_stocks()) if s.get("status") == "hold"]
    except Exception:
        holds = []
    hold_line = ("\n当前持仓：" + "、".join(f"{s['name']}({s['code']})" for s in holds[:12])) if holds else ""
    # 基线 v1.1: ✅式维度行(md), 建议单独传给统一卡的 👉建议区(emit_risk_dimension 合并)
    text = (f"✅ 涨停家数骤降：今 **{today_lu}** 家 ← 昨 **{prev_lu}** 家"
            f"（<font color='green'>**↓{drop_pct}%**</font>）{hold_line}")
    advice = "整板在抽血，强势股先走一部分"

    # 先写库(写库失败则不推, 避免重启重推) — 照 plunge_detector 的"先落库再推"模式。
    try:
        from backend.services import signal_specs
        await repository.save_signal(
            code=_EBB_CODE, name=str(cur.get("name") or "上证指数"),
            signal_id=_EBB_SIGNAL_ID, signal_name="大盘退潮·减仓提示",
            direction="plunge",
            price=0, detail=f"今{today_lu}家|昨{prev_lu}家|↓{drop_pct}%",
            user_id=_EBB_USER_ID,
            signal_group=signal_specs.group_of(_EBB_SIGNAL_ID),
        )
    except Exception as e:
        logger.error(f"[market_ebb] save_signal 失败, 跳过推送避免重启重推: {e}")
        return

    try:
        # v1.7.556 批次D: 退潮不再独推, 并入统一「大盘风控」卡(与溢价转负/当前风险状态合并去重)
        from backend.services import market_risk_controller
        await market_risk_controller.emit_risk_dimension("退潮", text, advice)
        logger.warning(f"[market_ebb] 退潮减仓提示已并入大盘风控卡: 涨停 {prev_lu}->{today_lu} (↓{drop_pct}%)")
    except Exception as e:
        logger.warning(f"[market_ebb] 推送失败: {e}")


# ── 强势退潮: 昨日涨停股今日平均溢价转负 = 打板/强势资金亏钱, 赚钱效应消失 ──
PREMIUM_EBB_THRESHOLD = -0.5     # yest_limit_up_premium ≤ 此值 → 退潮(今 -0.77 会触发, 可调)
_strength_alerted_date: str | None = None
_STRENGTH_SIGNAL_ID = "PLUNGE_STRENGTH_EBB"


def _premium_ebb(prem, threshold: float) -> bool:
    """昨日涨停今日溢价 ≤ 阈值 → 赚钱效应转亏。None 视为不触发。"""
    return prem is not None and prem <= threshold


async def detect_strength_ebb():
    global _strength_alerted_date
    if not is_trading_time():
        return
    now = datetime.now()
    if now.hour < 11:
        return
    today = now.strftime("%Y-%m-%d")
    if _strength_alerted_date == today:
        return
    cur = await repository.get_latest_emotion()
    if not cur or str(cur.get("trade_date") or "")[:10] != today:
        return
    prem = cur.get("yest_limit_up_premium")
    if not _premium_ebb(prem, PREMIUM_EBB_THRESHOLD):
        return

    # DB 去重(v1.7.565, 0703全系统排查): 溢价条件一旦成立会持续一整天, 纯内存哨兵在
    # 部署重启后即丢 → 大盘风控卡当日重推。仿 detect_market_ebb 加 DB 双保险。
    try:
        if await repository.signal_already_sent_today(_EBB_CODE, _STRENGTH_SIGNAL_ID, _EBB_USER_ID):
            _strength_alerted_date = today
            return
    except Exception as e:
        logger.error(f"[strength_ebb] DB 去重查询失败, 本轮跳过: {e}")
        return
    _strength_alerted_date = today

    # 基线 v1.1: ✅式维度行(md), 建议单独传给统一卡的 👉建议区
    text = (f"✅ 强势退潮：昨日涨停股今日平均溢价 "
            f"<font color='green'>**{prem:.2f}%**</font>，打板资金已转亏")
    advice = "别追高，手中高位股谨慎"

    # 先写库(写库失败则不推, 避免重启重推) — 同 detect_market_ebb。
    try:
        from backend.services import signal_specs
        await repository.save_signal(
            code=_EBB_CODE, name=str(cur.get("name") or "上证指数"),
            signal_id=_STRENGTH_SIGNAL_ID, signal_name="强势退潮·赚钱效应消失",
            direction="plunge",
            price=0, detail=f"昨涨停今溢价{prem:.2f}%|阈值{PREMIUM_EBB_THRESHOLD}%",
            user_id=_EBB_USER_ID,
            signal_group=signal_specs.group_of(_STRENGTH_SIGNAL_ID),
        )
    except Exception as e:
        logger.error(f"[strength_ebb] save_signal 失败, 跳过推送避免重启重推: {e}")
        return

    try:
        # v1.7.556 批次D: 溢价转负不再独推, 并入统一「大盘风控」卡(与退潮/当前风险状态合并去重)
        from backend.services import market_risk_controller
        await market_risk_controller.emit_risk_dimension("溢价", text, advice)
        logger.warning(f"[strength_ebb] 退潮提示已并入大盘风控卡: 昨涨停今溢价 {prem:.2f}%")
    except Exception as e:
        logger.warning(f"[strength_ebb] 推送失败: {e}")
