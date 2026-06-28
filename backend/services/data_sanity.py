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
ALERT_STALE_SEC = 360      # 自愈后仍 >360s(6min) 未更新才算异常
ALERT_MIN_COUNT = 5        # 异常票数达到此值才告警
ALERT_COOLDOWN = 1800      # 同类告警冷却 30min

_last_alert_at: float = 0.0


async def self_heal_stale_quotes():
    if not is_trading_time():
        return
    stale = await repository.get_stale_quote_codes(HEAL_STALE_SEC, limit=80)
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
    if h["stale"] >= ALERT_MIN_COUNT:
        problems.append(f"行情陈旧: {h['stale']}/{h['total']} 只 >6 分钟未更新")
    if h["null_price"] >= ALERT_MIN_COUNT:
        problems.append(f"行情缺失: {h['null_price']}/{h['total']} 只无价格")
    if not problems:
        return

    now = time.time()
    if now - _last_alert_at < ALERT_COOLDOWN:
        return
    _last_alert_at = now
    text = ("⚠️ 行情数据自检告警\n\n" + "\n".join(problems) +
            "\n\n盘中行情刷新可能异常(已自愈仍超阈值), 请检查新浪/同花顺数据源或后端日志。")
    try:
        from backend.services import notifier
        await notifier.send_wechat_text(text)
        logger.warning(f"[data_sanity] 告警已推送: {problems}")
    except Exception as e:
        logger.warning(f"[data_sanity] 告警推送失败: {e}")
