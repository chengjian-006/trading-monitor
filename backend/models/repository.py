"""v1.7.x: 业务逻辑全部按业务域拆到 backend/models/repo/, 本文件仅作 facade 重新 export.

调用方继续 `from backend.models import repository` 一切如旧, 不受影响.

已迁出 sub-modules:
  repo._db              : _fetchall / _fetchone / _execute / _executemany (内部 helper)
  repo.stocks           : 股票池 CRUD + quote/sector_rank 批更新
  repo.users            : 用户 + token_version + ths_path + profile
  repo.signals          : 信号 CRUD + 历史 + outcome 回填 + perf + stats + matrix
  repo.logs             : 操作日志查询 + 清理
  repo.signal_config    : 信号配置 + K线缓存
  repo.holdings         : FIFO 持仓成本 + 入仓日
  repo.market           : market_report / snapshot / popularity / overview / api_cache
  repo.executions       : signal_executions + report_feedback
  repo.scheduled_tasks  : 调度任务 CRUD + 运行状态
  repo.trades           : 交割单 + 持仓同步

新加 repository 操作时, 找到对应业务域 sub-module 直接写; 不要再回填进本 facade.
"""
# 内部底层 helper (sub-module 之间共享, 也兼容旧调用方直接 import)
from backend.models.database import get_pool  # noqa: F401
from backend.models.repo._db import _execute, _executemany, _fetchall, _fetchone  # noqa: F401
from backend.models.repo.trade_journal import (  # noqa: F401
    list_journal, create_journal, update_journal, delete_journal,
)

# 按业务域聚合 re-export
from backend.models.repo.stocks import (  # noqa: F401
    add_stock, remove_stock, purge_stock, update_stock, list_stocks, list_all_stocks,
    list_quotable_codes,
    batch_update_quotes, batch_update_core_quotes, batch_update_sector_rank,
    batch_update_board_strength,
    batch_update_sort_order,
    batch_update_stock_tags, get_pool_row, get_stale_quote_codes, count_quote_health,
    get_stock_popularity_rank, get_latest_popularity_rank, fetch_kline_close_batch,
)
from backend.models.repo.market_breadth import (  # noqa: F401
    save_market_breadth, get_latest_breadth,
)
from backend.models.repo.rally_track import (  # noqa: F401
    track_exists, create_track, get_holding_tracks, mark_half_sold,
    close_track, update_entry, set_days_held,
)
from backend.models.repo.signals import (  # noqa: F401
    save_signal, get_today_signals, get_today_signals_all, signal_already_sent_today,
    buy_signal_already_sent_today, get_sent_signal_keys_today,
    get_signals_history, get_signals_history_with_perf,
    fetch_kline_cache_for_codes,
    fetch_signals_pending_outcome, bulk_update_signal_outcome, get_signal_outcome_stats,
    get_buy_signal_p5_returns, get_buy_signal_span, get_buy_signals_on_date,
    fetch_signals_for_perf, bulk_insert_signal_perf, get_signal_perf_stats,
    get_signal_stats, get_signal_matrix,
    get_signals_by_code_date, get_signal_days_for_code, get_signals_by_code_since,
    get_stop_fires_by_code, get_key_signals_between, get_last_buy_model_batch,
    get_outcome_compare, get_weekly_outcome_trend, get_model_weekly_outcome,
    get_review_signal_list, set_eod_audit,
)
from backend.models.repo.users import (  # noqa: F401
    get_user_by_username, get_user_by_id, create_user, list_users, update_user, delete_user,
    get_token_version, increment_token_version, update_user_password,
    update_user_and_revoke_sessions, reset_user_password,
    get_user_ths_path, update_user_ths_path, update_user_profile,
)
from backend.models.repo.alerts import (  # noqa: F401
    list_alerts, list_alerts_by_code, list_active_alerts, get_alert,
    create_alert, update_alert, delete_alert, delete_alerts_for_stock, mark_triggered,
)
from backend.models.repo.logs import (  # noqa: F401
    add_log, get_logs, count_logs, get_log_actions, purge_old_logs, purge_old_logs_days,
)
from backend.models.repo.signal_config import (  # noqa: F401
    get_signal_config, save_signal_config, cache_klines, get_cached_klines, get_kline_counts,
)
from backend.models.repo.holdings import (  # noqa: F401
    get_holdings_cost, get_holdings_entry_date, get_holdings_entry_model, get_holdings_full_info,
    get_holdings_qty, get_holdings_took_half,
)
from backend.models.repo.market import (  # noqa: F401
    save_market_report, get_today_reports, get_latest_report, get_report_context,
    upsert_market_snapshot, get_market_snapshot,
    upsert_popularity_snapshot, get_popularity_snapshot,
    get_recent_popularity_dates, get_recent_hot_concepts,
    save_market_overview, get_market_overview,
    upsert_sparkline_snapshots, get_sparkline_snapshots, get_sparkline_snapshot_today,
    upsert_intraday_snapshots, get_intraday_snapshot, list_intraday_snapshot_dates,
    api_cache_set, api_cache_get,
)
from backend.models.repo.emotion import (  # noqa: F401
    save_emotion_snapshot, get_latest_emotion, get_emotion_history, get_last_emotion_before,
)
from backend.models.repo.near_buy import (  # noqa: F401
    save_near_buy_snapshot, get_near_buy_snapshot,
)
from backend.models.repo.executions import (  # noqa: F401
    upsert_signal_execution, delete_signal_execution, list_signal_executions,
    upsert_report_feedback, delete_report_feedback, list_report_feedback,
)
from backend.models.repo.scheduled_tasks import (  # noqa: F401
    list_scheduled_tasks, get_scheduled_task, update_scheduled_task,
    toggle_scheduled_task, update_task_run_status, count_scheduled_tasks,
)
from backend.models.repo.trades import (  # noqa: F401
    save_trade_records, get_all_trade_records, sync_positions_from_trades,
    has_import_today, get_latest_import_time, delete_trades_on_date, replace_trades_on_date,
)
from backend.models.repo.blogger_posts import (  # noqa: F401
    save_post, get_recent_posts, list_posts, mark_pushed,
)
from backend.models.repo.lark_coach_posts import (  # noqa: F401
    save_coach_post, list_coach_posts, get_coach_post_by_message_id,
    list_unrelayed_coach_posts, mark_coach_post_relayed,
)
from backend.models.repo.risk_ann import (  # noqa: F401
    save_risk_ann, list_risk_anns,
)
from backend.models.repo.fin_risk import (  # noqa: F401
    get_fin_risk, upsert_fin_risk, list_fin_risk,
)
from backend.models.repo.theme_heat import (  # noqa: F401
    save_theme_heat, get_theme_heat,
)
from backend.models.repo.wencai_pool import (  # noqa: F401
    upsert_wencai_strategy, set_wencai_error, list_wencai_pool, delete_wencai_pool_row,
)
from backend.models.repo.wencai_query import (  # noqa: F401
    pool_strategy_id, list_user_queries, get_query as get_wencai_query,
    add_query as add_wencai_query, update_query as update_wencai_query,
    delete_query as delete_wencai_query, list_all_enabled_queries,
)
from backend.models.repo.wencai_ask_preset import (  # noqa: F401
    DEFAULT_ASK_PRESETS, list_presets as list_ask_presets, get_preset as get_ask_preset,
    add_preset as add_ask_preset, update_preset as update_ask_preset,
    delete_preset as delete_ask_preset, reorder_presets as reorder_ask_presets,
    seed_defaults as seed_ask_presets,
)
from backend.models.repo.wencai_opinion import (  # noqa: F401
    insert_opinion as insert_wencai_opinion, list_opinions as list_wencai_opinions,
    delete_opinion as delete_wencai_opinion,
)
from backend.models.repo.sector_rotation import (  # noqa: F401
    upsert_sector_rotation, upsert_sector_prediction, get_sector_rotation,
    get_latest_sector_rotation,
)
from backend.models.repo.limit_up_pool import (  # noqa: F401
    upsert_daily as upsert_limit_up_daily, get_daily as get_limit_up_daily,
    list_dates as list_limit_up_dates, latest_date as latest_limit_up_date,
    concept_streak as limit_up_concept_streak,
)
from backend.models.repo.auction_pool import (  # noqa: F401
    save_auction_snapshots, get_auction_snapshots, get_auction_latest_date,
)
from backend.models.repo.model_backtest import (  # noqa: F401
    save_model_backtest, get_latest_model_backtest,
)
from backend.models.repo.model_winrate import (  # noqa: F401
    save_model_winrate, get_model_winrate,
    stage_model_winrate_code, staged_model_winrate_codes, staged_model_winrate_count,
    load_model_winrate_stage, clear_model_winrate_stage,
)
from backend.models.repo.holding_state_fwd import (  # noqa: F401
    save_holding_state_fwd, get_holding_state_fwd,
)
from backend.models.repo.gate_ab import (  # noqa: F401
    save_gate_ab, get_gate_ab,
)
from backend.models.repo.stock_names import (  # noqa: F401
    upsert_many as upsert_stock_names,
    get_names as get_stock_names,
    count as count_stock_names,
    all_names as all_stock_names,
)
from backend.models.repo.industry_map import (  # noqa: F401
    upsert_many as upsert_industry_map,
    load_all as load_industry_map,
    count as count_industry_map,
)
from backend.models.repo import backtest_jobs_db  # noqa: F401
from backend.models.repo.paper_trading import (  # noqa: F401
    get_or_create_account as paper_get_or_create_account,
    update_settings as paper_update_settings,
    reset_account as paper_reset_account,
    get_position as paper_get_position,
    list_positions as paper_list_positions,
    position_count as paper_position_count,
    sum_position_cost as paper_sum_position_cost,
    took_half_codes as paper_took_half_codes,
    signal_processed as paper_signal_processed,
    apply_fill as paper_apply_fill,
    record_failure as paper_record_failure,
    list_trades as paper_list_trades,
    upsert_equity as paper_upsert_equity,
    get_equity_curve as paper_get_equity_curve,
    realized_stats as paper_realized_stats,
    model_stats as paper_model_stats,
)
from backend.models.repo.coach_report import save_coach_report, get_coach_report  # noqa: F401
from backend.models.repo.stock_review import (  # noqa: F401
    save_stock_review, get_stock_review, count_reviews_today,
)
