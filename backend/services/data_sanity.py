"""行情数据自愈 + 合理性自检告警 — 防"静默错数"。

两个定时任务:
  self_heal_stale_quotes (90s): 盘中扫到 quote_updated_at 陈旧的自选票, 单独补刷核心行情。
      兜底 quote_refresher 偶发漏刷个别票(如德明利涨停却长期显示旧涨幅)。
  check_data_sanity (300s): 行情健康自检, 越界推企微告警(带冷却)。
      捕捉"整池停更 / 个别卡死 / 大面积无价"这类不报错但数据明显不对的情况。
"""
import logging
import time

from backend.core.trading_calendar import is_trading_time
from backend.models import repository
from backend import data_fetcher

logger = logging.getLogger(__name__)

HEAL_STALE_SEC = 150       # 盘中 >150s 未更新即补刷
HEAL_LIMIT = 500           # v1.7.562: 单轮补刷上限 80→500(覆盖整池), 原 80 只补不完大池致自检仍报陈旧
ALERT_STALE_SEC = 360      # 自愈后仍 >360s(6min) 未更新才算异常
ALERT_MIN_COUNT = 5        # 异常票数达到此值才告警
ALERT_COOLDOWN = 1800      # 同类告警冷却 30min
RESUME_GRACE_SEC = 180     # v1.7.562: 开盘/午休恢复宽限窗(秒), 窗内不判"陈旧"
CONTINUOUS_AM_SEC = 9 * 3600 + 30 * 60   # v1.7.x: 早盘连续竞价开始 09:30(集合竞价撮合09:25出价, 留5min余量)

_last_alert_at: float = 0.0


def _in_resume_grace(now=None, grace: int = RESUME_GRACE_SEC) -> bool:
    """是否处于交易时段刚恢复的宽限窗(开盘 / 午休回来 13:00 后的前几分钟)。开盘时刻取自 config trading_hours。

    v1.7.562: 午休/隔夜期间行情刷新暂停, 全池 quote_updated_at 天然停在上一时段末;
    恢复后头几分钟只要没把整池刷完, "陈旧>6min"就是必然读数而非真异常(0703 13:00
    自检告警 87/153 只陈旧即此结构性误报)。宽限窗内跳过陈旧判定, 行情缺失判定不受影响。
    """
    from datetime import datetime as _dt
    from backend.core.config import load_config
    n = now if now is not None else _dt.now()
    cur = n.hour * 3600 + n.minute * 60 + n.second
    periods = load_config().get("trading_hours") or [{"start": "09:15"}, {"start": "13:00"}]
    for p in periods:
        try:
            hh, mm = str(p.get("start", "")).split(":")
            start = int(hh) * 3600 + int(mm) * 60
        except (ValueError, AttributeError):
            continue
        if 0 <= cur - start < grace:
            return True
    return False


def _before_am_continuous(now=None) -> bool:
    """早盘连续竞价(09:30)之前 —— 集合竞价撮合前后全池无成交价是结构性正常, 非数据源故障。

    v1.7.594 起交易时段开盘前移到 09:15(为看竞价), is_trading_time 在 09:15~09:30 也为 True,
    但连续竞价前市场未产生最新价 → 全池 price 为空, "行情缺失"(null_price)全池告警是误报
    (0715 09:18 报 165/165 只无价即此)。此窗内跳过缺失判定; 下午 13:00 复盘已有价格不受影响
    (13:00 恒 > 09:30 → False); 09:30 后真缺失照常告警。开盘时刻取自 config trading_hours[0]。
    """
    from datetime import datetime as _dt
    from backend.core.config import load_config
    n = now if now is not None else _dt.now()
    cur = n.hour * 3600 + n.minute * 60 + n.second
    periods = load_config().get("trading_hours") or [{"start": "09:15"}]
    try:
        hh, mm = str(periods[0].get("start", "09:15")).split(":")
        am_start = int(hh) * 3600 + int(mm) * 60
    except (ValueError, AttributeError, IndexError):
        am_start = 9 * 3600 + 15 * 60
    return am_start <= cur < CONTINUOUS_AM_SEC


async def self_heal_stale_quotes():
    if not is_trading_time():
        return
    stale = await repository.get_stale_quote_codes(HEAL_STALE_SEC, limit=HEAL_LIMIT)
    if not stale:
        return
    try:
        quotes = await data_fetcher.get_realtime_quotes(stale)
    except Exception as e:
        logger.warning(f"[self_heal] 取行情失败: {e}")
        return
    core = [
        {"code": c, "price": quotes[c]["price"], "pct_change": quotes[c]["pct_change"],
         "amount": quotes[c]["amount"]}
        for c in stale if quotes.get(c)
    ]
    if core:
        await repository.batch_update_core_quotes(core)
    if len(core) < len(stale):
        logger.info(f"[self_heal] 补刷 {len(core)}/{len(stale)} 只; 仍有 {len(stale) - len(core)} 只取不到行情")
    else:
        logger.info(f"[self_heal] 补刷陈旧行情 {len(core)} 只")


async def check_data_sanity():
    global _last_alert_at
    # v1.7.389: 数据源健康预警(分时冻结回放/全市场快照无数据/日K缺今日bar)在此顺路 flush,
    # 放在交易时段闸门之前 — 收盘前最后几分钟上报的事件也能在盘后第一轮推出去
    from backend.services.data_health import flush_data_health
    await flush_data_health()
    if not is_trading_time():
        return
    try:
        h = await repository.count_quote_health(ALERT_STALE_SEC)
    except Exception as e:
        logger.warning(f"[data_sanity] 健康计数失败: {e}")
        return

    problems = []
    # 陈旧判定跳过"恢复宽限窗"(开盘/午休回来前3分钟) — 该窗内大面积陈旧是结构性必然, 非真异常
    if h["stale"] >= ALERT_MIN_COUNT and not _in_resume_grace():
        problems.append(f"行情陈旧 **{h['stale']}/{h['total']}** 只超6分钟未更新")
    # 行情缺失(全池无价): 早盘连续竞价(09:30)前是撮合前结构性正常, 跳过; 09:30后才是真缺失
    if h["null_price"] >= ALERT_MIN_COUNT and not _before_am_continuous():
        problems.append(f"行情缺失 **{h['null_price']}/{h['total']}** 只无价格")
    if not problems:
        return

    now = time.time()
    if now - _last_alert_at < ALERT_COOLDOWN:
        return
    _last_alert_at = now
    text = build_sanity_alert_text(problems)
    try:
        from backend.services import notifier
        await notifier.send_wechat_text(text)
        logger.warning(f"[data_sanity] 告警已推送: {problems}")
    except Exception as e:
        logger.warning(f"[data_sanity] 告警推送失败: {e}")


def build_sanity_alert_text(problems: list[str]) -> str:
    """自检告警正文(基线 v1.1 轻处理, 保持纯文本通道): 结论前置(实测值加粗) + 👉建议。"""
    return ("⚠️ 行情数据自检告警\n\n" + "\n".join(problems) +
            "\n\n盘中行情刷新可能异常(已自愈仍超阈值)。\n"
            "👉 **检查新浪/同花顺数据源或后端日志**")
