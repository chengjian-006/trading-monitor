"""Registry mapping handler name strings to actual async functions."""

import asyncio
import logging
import time
from datetime import datetime

from backend.services.scanner import scan_stock_pool
from backend.services.market_report import run_market_report
from backend.services.quote_refresher import refresh_quotes
from backend.services.market_data_refresher import refresh_market_data
from backend.services.popularity_refresher import refresh_popularity, refresh_popularity_full_ai, record_daily_popularity
from backend.services.plunge_detector import detect_plunge
from backend.services.sector_leader import refresh_sector_leaders
from backend.services.weak_extreme_scanner import scan_weak_extreme_snapshot
from backend.services.capital_inflow_scanner import scan_capital_inflow
from backend.services.strength_quality_scanner import scan_strength_quality_snapshot
from backend.services.sparkline_prefetcher import prefetch_intraday_sparklines
# run_post_close_summary 已下线(15:05盘后汇总, 并入晚盘复盘总结)
from backend.services.api_health import check_all_api_health
from backend.services.attack_direction_analyst import run_attack_direction_analysis
from backend.services.auction_summary_analyst import run_auction_summary, run_auction_0926
from backend.services.auction_sector_strength import run_auction_sector_strength
from backend.services.market_overview_refresher import refresh_market_overview
from backend.services.alert_throttle import flush_all as flush_alert_throttle
from backend.services.signal_outcome_backfill import backfill_signal_outcomes
from backend.services.signal_perf_snapshot import snapshot_signal_perf
from backend.services.stock_tag_refresher import refresh_stock_tags
from backend.services.emotion_refresher import refresh_emotion_snapshot
from backend.services.intraday_snapshot_freezer import freeze_intraday_snapshots
from backend.services.review_summary import run_review_summary
from backend.services.market_breadth_refresher import refresh_market_breadth
from backend.services.rally_reminder import rally_reminder_tick, rally_reminder_eod
from backend.services.holding_guard import holding_guard_tick
from backend.services.stop_escalation import stop_escalation_tick
from backend.services.ma_break_watch import run_ma_break_watch
from backend.services.ma_touch_alert import run_ma_touch_alert
from backend.services.second_surge_scanner import run_second_surge_scan
from backend.services.sector_cocrash_guard import run_sector_cocrash_watch
from backend.services.industry_map_refresher import run_industry_map_refresh
from backend.services.market_risk_controller import market_risk_eod, market_risk_intraday, market_risk_realtime
from backend.services.data_cross_checker import run_cross_check
from backend.services.blogger_post_scanner import scan_blogger_posts
from backend.services.wencai_scanner import scan_wencai
from backend.services.near_buy_refresher import refresh_near_buy_snapshot
from backend.services.theme_heat_refresher import refresh_theme_heat
from backend.services.sector_strength_scanner import refresh_sector_strength
from backend.services.model_winrate_refresher import refresh_model_winrate
from backend.services.kline_5m_appender import append_kline_5m
from backend.services.auction_pool_refresher import record_auction_pool_snapshot
from backend.services.auction_strength_selfcheck import run_auction_strength_selfcheck
from backend.services.model_backtest_weekly import run_model_backtest_weekly
from backend.services.log_cleanup import cleanup_old_logs
from backend.services.data_sanity import self_heal_stale_quotes, check_data_sanity
from backend.services.market_ebb_detector import detect_market_ebb, detect_strength_ebb
from backend.services.sector_rotation_scanner import scan_sector_rotation, predict_sector_next_day
from backend.services.limit_up_archive import run_limit_up_daily
from backend.services.trade_round_builder import rebuild_user_rounds
from backend.services.paper_equity import snapshot_paper_equity
from backend.services.paper_guard import paper_guard_tick
from backend.services.signal_eod_audit import run_signal_eod_audit
from backend.services.custom_alert_scanner import check_custom_alerts
from backend.services.risk_announcement_scanner import scan_risk_announcements
from backend.services.financial_risk_scanner import scan_financial_risk
from backend.services.blackswan_alerts import scan_blackswan_alerts
from backend.services.disclosure_reminder import refresh_disclosure_calendar  # run_disclosure_reminder已下线(披露并入晚盘复盘)
from backend.services.earnings_forecast_scan import run_earnings_forecast_scan
from backend.services.stock_names_refresher import refresh_stock_names
from backend.services.holding_brief import refresh_holding_state_fwd, run_holding_evening_report
# run_tail_decision_1440 已下线(14:40尾盘决策, 用户拍板精简盘后推送)
from backend.services.system_health import run_system_health_digest
from backend.services.morning_focus import run_morning_focus
from backend.services.push_health_report import run_push_health_report

logger = logging.getLogger(__name__)


async def flush_alert_throttle_and_storm():
    """既有 60s 周期 flush 任务(alert_throttle_flush)扩展: 节流缓冲 + 风暴聚合窗口到期兜底。
    storm_aggregator 主结算靠 90s 一次性定时器, 这里只兜「定时器丢失/入队时无事件循环」。"""
    await flush_alert_throttle()
    try:
        from backend.services.storm_aggregator import flush_expired
        await flush_expired()
    except Exception as e:
        logger.warning(f"[storm] 周期兜底 flush 异常: {e}")


async def rebuild_trade_rounds():
    """收盘后给所有用户重建交易回合 (FIFO 聚合交割单 + 归因买点)。"""
    from backend.models import repository

    users = await repository.list_users()
    if not users:
        return
    for user in users:
        user_id = user["id"]
        try:
            await rebuild_user_rounds(user_id)
        except Exception as e:
            logger.warning(f"[rounds] 重建交易回合失败 user={user_id}: {e}")

TASK_HANDLERS: dict[str, object] = {
    "scan_stock_pool": scan_stock_pool,
    "run_market_report": run_market_report,
    "refresh_quotes": refresh_quotes,
    "refresh_market_data": refresh_market_data,
    "refresh_popularity": refresh_popularity,
    "refresh_popularity_full_ai": refresh_popularity_full_ai,
    "detect_plunge": detect_plunge,
    "refresh_sector_leaders": refresh_sector_leaders,
    "scan_weak_extreme_snapshot": scan_weak_extreme_snapshot,
    "scan_capital_inflow": scan_capital_inflow,
    "scan_strength_quality_snapshot": scan_strength_quality_snapshot,
    "prefetch_intraday_sparklines": prefetch_intraday_sparklines,
    "check_all_api_health": check_all_api_health,
    "run_attack_direction_analysis": run_attack_direction_analysis,
    "run_auction_summary": run_auction_summary,
    "run_auction_0926": run_auction_0926,
    "run_auction_sector_strength": run_auction_sector_strength,
    "refresh_market_overview": refresh_market_overview,
    "flush_alert_throttle": flush_alert_throttle_and_storm,
    "backfill_signal_outcomes": backfill_signal_outcomes,
    "snapshot_signal_perf": snapshot_signal_perf,
    "refresh_stock_tags": refresh_stock_tags,
    "refresh_emotion_snapshot": refresh_emotion_snapshot,
    "freeze_intraday_snapshots": freeze_intraday_snapshots,
    "run_review_summary": run_review_summary,
    "refresh_market_breadth": refresh_market_breadth,
    "rally_reminder_tick": rally_reminder_tick,
    "rally_reminder_eod": rally_reminder_eod,
    "holding_guard_tick": holding_guard_tick,
    "stop_escalation_tick": stop_escalation_tick,
    "run_ma_break_watch": run_ma_break_watch,
    "run_ma_touch_alert": run_ma_touch_alert,
    "run_second_surge_scan": run_second_surge_scan,
    "run_sector_cocrash_watch": run_sector_cocrash_watch,
    "run_industry_map_refresh": run_industry_map_refresh,
    "market_risk_eod": market_risk_eod,
    "market_risk_intraday": market_risk_intraday,
    "market_risk_realtime": market_risk_realtime,
    "run_cross_check": run_cross_check,
    "scan_blogger_posts": scan_blogger_posts,
    "scan_wencai": scan_wencai,
    "refresh_near_buy_snapshot": refresh_near_buy_snapshot,
    "refresh_theme_heat": refresh_theme_heat,
    "refresh_sector_strength": refresh_sector_strength,
    "refresh_model_winrate": refresh_model_winrate,
    "append_kline_5m": append_kline_5m,
    "record_auction_pool_snapshot": record_auction_pool_snapshot,
    "run_auction_strength_selfcheck": run_auction_strength_selfcheck,
    "run_model_backtest_weekly": run_model_backtest_weekly,
    "cleanup_old_logs": cleanup_old_logs,
    "self_heal_stale_quotes": self_heal_stale_quotes,
    "check_data_sanity": check_data_sanity,
    "detect_market_ebb": detect_market_ebb,
    "detect_strength_ebb": detect_strength_ebb,
    "scan_sector_rotation": scan_sector_rotation,
    "predict_sector_next_day": predict_sector_next_day,
    "run_limit_up_daily": run_limit_up_daily,
    "rebuild_trade_rounds": rebuild_trade_rounds,
    "snapshot_paper_equity": snapshot_paper_equity,
    "paper_guard_tick": paper_guard_tick,
    "signal_eod_audit": run_signal_eod_audit,
    "record_daily_popularity": record_daily_popularity,
    "check_custom_alerts": check_custom_alerts,
    "scan_risk_announcements": scan_risk_announcements,
    "scan_financial_risk": scan_financial_risk,
    "scan_blackswan_alerts": scan_blackswan_alerts,
    "refresh_stock_names": refresh_stock_names,
    "refresh_holding_state_fwd": refresh_holding_state_fwd,
    "run_holding_evening_report": run_holding_evening_report,
    "run_system_health_digest": run_system_health_digest,
    "refresh_disclosure_calendar": refresh_disclosure_calendar,
    "run_earnings_forecast_scan": run_earnings_forecast_scan,
    "run_morning_focus": run_morning_focus,
    "run_push_health_report": run_push_health_report,
}


# 任务执行统一超时保护: 单 worker 架构下某任务僵死(外部接口挂起等)会永久占坑
# (max_instances=1 只防重入不防僵死), 用 asyncio.wait_for 兜底中止。
# 超时 = 一次失败, 走既有 失败落库/连续失败计数/告警 链路。
DEFAULT_TASK_TIMEOUT = 30 * 60       # 普通任务默认 30 分钟
LONG_TASK_TIMEOUT = 3 * 60 * 60      # 已知长任务放宽到 3 小时(宁长勿短, 误杀正常任务比放过僵死更糟)
OVERNIGHT_TIMEOUT = 6 * 60 * 60      # 通宵级(21:00起跑, 次日9点前无竞争, 小ECS慢也够跑完)

# 已知长任务清单 (handler 名 -> 超时秒数)
LONG_TASK_TIMEOUTS: dict[str, int] = {
    "run_model_backtest_weekly": LONG_TASK_TIMEOUT,    # 模型周回测(全市场多模型)
    "refresh_model_winrate": OVERNIGHT_TIMEOUT,        # 胜率重算(每晚21:00, 5分钟诚实口径全市场逐票, 小ECS约2-4h)
    "refresh_holding_state_fwd": LONG_TASK_TIMEOUT,    # 持仓态前向分布刷新(每周全市场扫描)
    "backfill_signal_outcomes": LONG_TASK_TIMEOUT,     # 信号闭环收益回填(逐信号拉K线)
    "append_kline_5m": OVERNIGHT_TIMEOUT,              # 5分钟K线每日追加(baostock逐票串行, 首夜补1月缺口更慢)
}


def get_task_timeout(handler_name: str) -> float:
    """按 handler 名查执行超时秒数: 长任务表命中给宽松值, 否则默认 30 分钟。"""
    return LONG_TASK_TIMEOUTS.get(handler_name, DEFAULT_TASK_TIMEOUT)


# v1.7.x: 调度任务失败告警阈值
# consecutive_failures >= ALERT_THRESHOLD 时推一条企微; 同 job 告警冷却 ALERT_COOLDOWN_SECONDS
ALERT_THRESHOLD = 3
ALERT_COOLDOWN_SECONDS = 3600

# 进程级冷却记录 (重启会丢, 但重启后第一次告警重新触发也无妨)
_last_alert_at: dict[str, float] = {}


def build_task_failure_text(job_id: str, handler_name: str, count: int, err_msg: str) -> str:
    """任务失败告警正文(基线 v1.1 轻处理, 保持纯文本通道): 结论前置 + 任务名加粗 + 👉建议。"""
    return (
        f"⚠️ 调度任务连续失败\n\n"
        f"**{job_id}** 连续失败 **{count}** 次（阈值 {ALERT_THRESHOLD}）\n"
        f"handler: {handler_name}\n"
        f"最近错误: {err_msg[:200]}\n\n"
        f"👉 **检查 backend 日志或对应数据源状态**"
    )


async def _maybe_alert_task_failure(job_id: str, handler_name: str, count: int, err_msg: str):
    """连续失败达到阈值时推送告警(纯文本通道不变), 同 job 1 小时内不重复推。"""
    if count < ALERT_THRESHOLD:
        return
    now = time.time()
    last = _last_alert_at.get(job_id, 0.0)
    if now - last < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert_at[job_id] = now
    text = build_task_failure_text(job_id, handler_name, count, err_msg)
    try:
        from backend.services import notifier
        await notifier.send_wechat_text(text)
        logger.warning(f"[task_alert] {job_id} 失败告警已推送 (consecutive={count})")
    except Exception as e:
        logger.warning(f"[task_alert] {job_id} 告警推送失败: {e}")


async def wrapped_handler(job_id: str, handler_name: str, timeout: float | None = None):
    from backend.models import repository

    handler = TASK_HANDLERS.get(handler_name)
    if not handler:
        logger.error(f"Unknown handler: {handler_name}")
        return
    effective_timeout = timeout if timeout is not None else get_task_timeout(handler_name)
    try:
        try:
            await asyncio.wait_for(handler(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            # 转成可读信息, 作为一次普通失败走下方既有 失败落库/计数/告警 链路
            raise TimeoutError(f"任务 {handler_name} 超时({effective_timeout:g}s)被中止") from None
        await repository.update_task_run_status(job_id, datetime.now(), "success")
    except Exception as e:
        logger.exception(f"Task {job_id} failed: {e}")
        err_msg = f"{type(e).__name__}: {e}"
        try:
            count = await repository.update_task_run_status(job_id, datetime.now(), "error", err_msg)
        except Exception as e2:
            logger.warning(f"[task_alert] {job_id} 写入失败状态时也异常, 跳过告警: {e2}")
            return
        await _maybe_alert_task_failure(job_id, handler_name, count, err_msg)
