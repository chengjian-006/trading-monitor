# -*- coding: utf-8 -*-
from backend.services.review_metrics import (
    build_category_where, extract_trade_plan, parse_exit_plan, compute_kline_returns,
    returns_from_perf, returns_from_outcome, summarize_rows,
)


def test_build_category_where_buy_excludes_sector():
    w = build_category_where(["buy"])
    assert "direction='buy'" in w and "BUY\\_%" in w
    # 板块 SECTOR_ 的 direction 也是 buy, 必须靠前缀分开
    assert "SECTOR" not in w


def test_build_category_where_multi_or():
    w = build_category_where(["buy", "sell", "reduce"])
    assert " OR " in w
    assert "direction='sell'" in w and "direction='reduce'" in w


def test_build_category_where_empty_defaults_to_buy():
    assert "BUY\\_%" in build_category_where([])
    assert "BUY\\_%" in build_category_where(["bogus"])


def test_extract_trade_plan():
    detail = "昨缩量 → 今日放量突破 | 交易计划: +7%卖半/剩半破MA10×0.98/-6%止损/T+10时停 | 成交额 2.6亿"
    assert extract_trade_plan(detail) == "+7%卖半/剩半破MA10×0.98/-6%止损/T+10时停"
    assert extract_trade_plan("无计划段") == ""
    assert extract_trade_plan(None) == ""


def test_parse_exit_plan_full():
    ep = parse_exit_plan("+7%卖半/剩半破MA10×0.98/-6%止损/T+10时停", 100.0)
    assert ep["tp_pct"] == 7.0 and ep["tp_action"] == "卖半"
    assert ep["tp_price"] == 107.0
    assert ep["sl_pct"] == 6.0 and ep["sl_price"] == 94.0
    assert ep["time_stop_days"] == 10
    assert "剩半破MA10×0.98" in ep["other_exit"] and "T+10时停" in ep["other_exit"]


def test_parse_exit_plan_reduce_only():
    ep = parse_exit_plan("+15%减半/-7%止损", 200.0)
    assert ep["tp_pct"] == 15.0 and ep["tp_action"] == "减半" and ep["tp_price"] == 230.0
    assert ep["sl_pct"] == 7.0 and ep["sl_price"] == 186.0
    assert ep["time_stop_days"] is None and ep["other_exit"] == ""


def test_parse_exit_plan_empty():
    ep = parse_exit_plan("", 100.0)
    assert ep["tp_pct"] is None and ep["sl_pct"] is None and ep["other_exit"] == ""
    assert parse_exit_plan(None, None)["tp_price"] is None


def test_compute_kline_returns_basic():
    kl = [
        {"trade_date": "2026-06-01", "high": 105, "low": 98, "close": 102},
        {"trade_date": "2026-06-02", "high": 110, "low": 101, "close": 108},
        {"trade_date": "2026-06-03", "high": 109, "low": 103, "close": 104},
    ]
    r = compute_kline_returns(100.0, "2026-06-01", kl)
    assert round(r["cur_ret_pct"], 2) == 4.0
    assert round(r["max_gain_pct"], 2) == 10.0
    assert round(r["max_dd_pct"], 2) == -2.0
    assert round(r["t1_pct"], 2) == 8.0      # rows[1].close=108
    assert r["t3_pct"] is None               # 不足4根
    assert r["t5_pct"] is None


def test_compute_kline_returns_filters_before_trigger_and_handles_empty():
    kl = [{"trade_date": "2026-05-30", "high": 200, "low": 100, "close": 150}]
    r = compute_kline_returns(100.0, "2026-06-01", kl)   # 全在触发日之前
    assert r["cur_ret_pct"] is None and r["max_gain_pct"] is None
    assert compute_kline_returns(None, "2026-06-01", kl)["cur_ret_pct"] is None


def test_returns_from_perf():
    perf = [
        {"day_offset": 1, "high_pct": 3.0, "low_pct": -1.0, "close_pct": 2.0},
        {"day_offset": 2, "high_pct": 6.0, "low_pct": -4.0, "close_pct": 5.0},
    ]
    r = returns_from_perf(perf)
    assert r["max_gain_pct"] == 6.0 and r["max_dd_pct"] == -4.0
    assert r["t1_pct"] == 2.0 and r["t3_pct"] is None
    assert r["cur_ret_pct"] == 5.0          # 最大 day_offset 的 close_pct
    assert returns_from_perf([]) is None


def test_returns_from_outcome():
    r = returns_from_outcome({"outcome_p1_pct": 1.0, "outcome_p3_pct": 3.0, "outcome_p5_pct": 5.0})
    assert r["t5_pct"] == 5.0 and r["cur_ret_pct"] == 5.0
    assert returns_from_outcome({"outcome_p1_pct": None, "outcome_p3_pct": None, "outcome_p5_pct": None}) is None


def test_summarize_rows():
    rows = [
        {"signal_id": "BUY_X", "signal_name": "X", "cur_ret_pct": 10.0, "max_gain_pct": 12.0,
         "max_dd_pct": -3.0, "t5_pct": 9.0, "outcome": "success"},
        {"signal_id": "BUY_X", "signal_name": "X", "cur_ret_pct": -4.0, "max_gain_pct": 2.0,
         "max_dd_pct": -6.0, "t5_pct": None, "outcome": "fail"},
        {"signal_id": "SECTOR_Y", "signal_name": "Y", "cur_ret_pct": None},  # 板块不计入
    ]
    s = summarize_rows(rows)
    by = {r["signal_id"]: r for r in s}
    assert by["BUY_X"]["count"] == 2
    assert by["BUY_X"]["win_rate"] == 50.0
    assert by["BUY_X"]["avg_cur_ret"] == 3.0
    assert by["BUY_X"]["avg_t5"] == 9.0           # 只 1 个非空
    assert by["BUY_X"]["success_rate"] == 50.0
    assert "SECTOR_Y" not in by                    # 无收益不汇总
    assert by["__ALL__"]["count"] == 2
