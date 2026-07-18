# -*- coding: utf-8 -*-
"""回合 MFE/MAE 回填 (v1.7.685) — 纯函数, 不连库.

重点覆盖"窗口约定"这条最容易出错的规则: 入场日不计、出场日计入。
"""
from datetime import date

from backend.routers.trade_analysis import _round_summary
from backend.services.trade_round_builder import attach_excursions


def _bar(d, hi, lo):
    return {"trade_date": date.fromisoformat(d), "high": hi, "low": lo}


def _round(entry=10.0, open_d="2026-03-02", close_d="2026-03-05", pnl_pct=None):
    return {"entry_price": entry, "open_date": date.fromisoformat(open_d),
            "close_date": date.fromisoformat(close_d) if close_d else None,
            "realized_pnl_pct": pnl_pct, "status": "closed" if close_d else "open"}


def test_entry_day_excluded_from_mfe_mae():
    """入场日的极值不能算进去 —— A股T+1当天卖不掉, 且日线分不出买入前后。"""
    bars = [
        _bar("2026-03-02", 13.0, 7.0),    # 入场日: 极端高低, 必须被忽略
        _bar("2026-03-03", 11.0, 9.5),
        _bar("2026-03-04", 11.5, 9.0),
        _bar("2026-03-05", 10.5, 9.8),
    ]
    r = _round()
    attach_excursions(r, bars)
    assert r["mfe_pct"] == 15.0                      # 11.5 而非入场日的 13.0
    assert r["mfe_date"] == date(2026, 3, 4)
    assert r["mae_pct"] == -10.0                     # 9.0 而非入场日的 7.0
    assert r["mae_date"] == date(2026, 3, 4)
    assert r["holding_days"] == 4                    # 含入场日与出场日


def test_exit_day_included():
    """出场日要计入 —— 当天本可以卖在更高价, 这正是要衡量的。"""
    bars = [
        _bar("2026-03-02", 10.2, 9.9),
        _bar("2026-03-03", 10.3, 9.9),
        _bar("2026-03-04", 12.0, 9.9),   # 出场日冲高
    ]
    r = _round(close_d="2026-03-04")
    attach_excursions(r, bars)
    assert r["mfe_pct"] == 20.0 and r["mfe_date"] == date(2026, 3, 4)


def test_bars_outside_window_ignored():
    """出场后的行情不属于这个回合, 不能算进 MFE。"""
    bars = [
        _bar("2026-03-02", 10.1, 9.9),
        _bar("2026-03-03", 10.5, 9.8),
        _bar("2026-03-06", 20.0, 9.0),   # 已清仓后暴涨, 与你无关
    ]
    r = _round(close_d="2026-03-03")
    attach_excursions(r, bars)
    assert r["mfe_pct"] == 5.0
    assert r["holding_days"] == 2


def test_open_round_uses_all_bars_to_date():
    """持仓中的回合(close_date=None)统计到最新一根。"""
    bars = [_bar("2026-03-02", 10.1, 9.9), _bar("2026-03-03", 10.5, 9.0),
            _bar("2026-03-04", 12.0, 9.5)]
    r = _round(close_d=None)
    attach_excursions(r, bars)
    assert r["mfe_pct"] == 20.0 and r["mae_pct"] == -10.0


def test_max_drawdown_is_giveback_from_running_peak():
    """最大回撤 = 自当时最高点回落, 不是自入场价回落。"""
    bars = [
        _bar("2026-03-02", 10.0, 10.0),
        _bar("2026-03-03", 15.0, 14.0),   # 冲到 15
        _bar("2026-03-04", 14.0, 12.0),   # 回落到 12 → 自峰值 -20%
    ]
    r = _round()
    attach_excursions(r, bars)
    assert r["max_drawdown_pct"] == -20.0


def test_same_day_round_falls_back_to_entry_day():
    """当日建仓当日清仓(脏数据)时窗口为空, 退回用入场日而不是留 NULL。"""
    bars = [_bar("2026-03-02", 11.0, 9.0)]
    r = _round(close_d="2026-03-02")
    attach_excursions(r, bars)
    assert r["mfe_pct"] == 10.0 and r["mae_pct"] == -10.0
    assert r["holding_days"] == 1


def test_no_bars_leaves_fields_absent():
    """查不到K线时不写字段(保持 NULL), 不能写 0 冒充有数据。"""
    r = _round()
    attach_excursions(r, [])
    assert "mfe_pct" not in r and "holding_days" not in r


def test_bad_entry_price_is_skipped():
    r = _round(entry=0)
    attach_excursions(r, [_bar("2026-03-03", 11.0, 9.0)])
    assert "mfe_pct" not in r


# ── 汇总层 ──

def test_summary_efficiency_and_zones():
    rows = [
        # 吃满: 浮盈10%, 兑现8% → 出场效率80%
        {"status": "closed", "mfe_pct": 10.0, "mae_pct": -2.0,
         "realized_pnl_pct": 8.0, "holding_days": 5, "id": 1},
        # 落袋太早: 浮盈20%, 只兑现2%
        {"status": "closed", "mfe_pct": 20.0, "mae_pct": -1.0,
         "realized_pnl_pct": 2.0, "holding_days": 3, "id": 2},
        # 浮盈坐成亏损: 曾经+8%, 最后亏
        {"status": "closed", "mfe_pct": 8.0, "mae_pct": -12.0,
         "realized_pnl_pct": -9.0, "holding_days": 20, "id": 3},
    ]
    s = _round_summary(rows)
    assert s["closed"] == 3 and s["graded"] == 3
    assert s["zone_good"] == 1 and s["zone_sold_early"] == 1 and s["zone_gave_back"] == 1
    assert s["exit_efficiency"] == 45.0          # (80 + 10) / 2
    # 持仓时长比: 亏损单20天 / 盈利单(5+3)/2=4天 → 5.0, 远大于1 = 截断利润
    assert s["holding_ratio"] == 5.0
    assert s["avg_win_holding_days"] == 4.0 and s["avg_loss_holding_days"] == 20.0


def test_summary_returns_none_not_zero_when_no_sample():
    """没样本时给 None, 不能给 0 —— 0% 效率和"没数据"是两回事。"""
    s = _round_summary([{"status": "open", "id": 1}])
    assert s["entry_efficiency"] is None and s["exit_efficiency"] is None
    assert s["holding_ratio"] is None and s["graded"] == 0
