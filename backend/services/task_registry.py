"""Registry mapping handler name strings to actual async functions."""

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
from backend.services.post_close_summary import run_post_close_summary
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
from backend.services.market_risk_controller import market_risk_eod, market_risk_intraday, market_risk_realtime
from backend.services.data_cross_checker import run_cross_check
from backend.services.blogger_post_scanner import scan_blogger_posts
from backend.services.wencai_scanner import scan_wencai
from backend.services.near_buy_refresher import refresh_near_buy_snapshot
from backend.services.theme_heat_refresher import refresh_theme_heat
from backend.services.sector_strength_scanner import refresh_sector_strength
from backend.services.model_winrate_refresher import refresh_model_winrate
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
from backend.services.signal_eod_audit import run_signal_eod_audit
from backend.services.custom_alert_scanner import check_custom_alerts
from backend.services.risk_announcement_scanner import scan_risk_announcements
from backend.services.financial_risk_scanner import scan_financial_risk
from backend.services.blackswan_alerts import scan_blackswan_alerts
from backend.services.disclosure_reminder import refresh_disclosure_calendar, run_disclosure_reminder
from backend.services.earnings_forecast_scan import run_earnings_forecast_scan
from backend.services.stock_names_refresher import refresh_stock_names
from backend.services.holding_brief import refresh_holding_state_fwd, run_holding_evening_report
from backend.services.tail_decision import run_tail_decision_1440
from backend.services.system_health import run_system_health_digest

logger = logging.getLogger(__name__)


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
    "run_post_close_summary": run_post_close_summary,
    "check_all_api_health": check_all_api_health,
    "run_attack_direction_analysis": run_attack_direction_analysis,
    "run_auction_summary": run_auction_summary,
    "run_auction_0926": run_auction_0926,
    "run_auction_sector_strength": run_auction_sector_strength,
    "refresh_market_overview": refresh_market_overview,
    "flush_alert_throttle": flush_alert_throttle,
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
    "signal_eod_audit": run_signal_eod_audit,
    "record_daily_popularity": record_daily_popularity,
    "check_custom_alerts": check_custom_alerts,
    "scan_risk_announcements": scan_risk_announcements,
    "scan_financial_risk": scan_financial_risk,
    "scan_blackswan_alerts": scan_blackswan_alerts,
    "refresh_stock_names": refresh_stock_names,
    "refresh_holding_state_fwd": refresh_holding_state_fwd,
    "run_holding_evening_report": run_holding_evening_report,
    "run_tail_decision_1440": run_tail_decision_1440,
    "run_system_health_digest": run_system_health_digest,
    "refresh_disclosure_calendar": refresh_disclosure_calendar,
    "run_disclosure_reminder": run_disclosure_reminder,
    "run_earnings_forecast_scan": run_earnings_forecast_scan,
}


# v1.7.x: 调度任务失败告警阈值
# consecutive_failures >= ALERT_THRESHOLD 时推一条企微; 同 job 告警冷却 ALERT_COOLDOWN_SECONDS
ALERT_THRESHOLD = 3
ALERT_COOLDOWN_SECONDS = 3600

# 进程级冷却记录 (重启会丢, 但重启后第一次告警重新触发也无妨)
_last_alert_at: dict[str, float] = {}


async def _maybe_alert_task_failure(job_id: str, handler_name: str, count: int, err_msg: str):
    """连续失败达到阈值时推送企微告警, 同 job 1 小时内不重复推。"""
    if count < ALERT_THRESHOLD:
        return
    now = time.time()
    last = _last_alert_at.get(job_id, 0.0)
    if now - last < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert_at[job_id] = now
    text = (
        f"⚠️ 调度任务连续失败告警\n\n"
        f"任务: {job_id}\n"
        f"handler: {handler_name}\n"
        f"连续失败次数: {count}\n"
        f"最近错误: {err_msg[:200]}\n\n"
        f"已超过阈值 {ALERT_THRESHOLD}, 请检查 backend 日志或对应数据源状态"
    )
    try:
        from backend.services import notifier
        await notifier.send_wechat_text(text)
        logger.warning(f"[task_alert] {job_id} 失败告警已推送 (consecutive={count})")
    except Exception as e:
        logger.warning(f"[task_alert] {job_id} 告警推送失败: {e}")


async def wrapped_handler(job_id: str, handler_name: str):
    from backend.models import repository

    handler = TASK_HANDLERS.get(handler_name)
    if not handler:
        logger.error(f"Unknown handler: {handler_name}")
        return
    try:
        await handler()
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
