"""Tests for stock_facts — individual stock fact sheet constructor."""
from backend.services.ai_advisor.stock_facts import build_stock_facts


def test_holding_context_present_when_held():
    f = build_stock_facts("300085", "银之杰",
        signals=[{"signal_name": "回踩MA10（右侧）", "trigger_date": "2026-07-01", "direction": "buy"}],
        winrate={"BUY_MA10": {"model_name": "回踩MA10", "win_rate_3m": 60.0, "n_3m": 100}},
        fin_risk=None, sector={"board_strength": 2, "sector_rank": 3, "theme_heat": []},
        holding={"cost": 30.0, "float_pct": 3.0, "entry_model": "回踩MA10"},
        near_buy=None)
    assert f["holding"]["is_holding"] is True
    assert f["holding"]["cost"] == 30.0
    assert f["risk_flags"]["has_data"] is False


def test_non_holding_and_no_risk_data():
    f = build_stock_facts("600000", "浦发银行",
        signals=[], winrate={}, fin_risk=None,
        sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    assert f["holding"]["is_holding"] is False
    assert f["risk_flags"]["has_data"] is False
    assert f["signal_history"]["n"] == 0


def test_model_winrate_backfilled_from_signal_history():
    f = build_stock_facts("300085", "银之杰",
        signals=[{"signal_name": "回踩MA10（右侧）", "trigger_date": "2026-07-01", "direction": "buy"}],
        winrate={"BUY_MA10": {"model_name": "回踩MA10", "win_rate_3m": 60.0, "n_3m": 100}},
        fin_risk=None, sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    names = [m["model_name"] for m in f["model_winrate"]]
    assert "回踩MA10" in names


def test_risk_flags_parsed_from_flags_json():
    """Test that flags are correctly parsed from flags_json column (JSON string)."""
    fin_risk = {"score": 55, "flags_json": '["商誉过高", "存贷双高"]'}
    f = build_stock_facts("300085", "银之杰",
        signals=[], winrate={}, fin_risk=fin_risk,
        sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    assert f["risk_flags"]["has_data"] is True
    assert f["risk_flags"]["score"] == 55
    assert f["risk_flags"]["flags"] == ["商誉过高", "存贷双高"]


def test_risk_flags_dirty_data_does_not_crash():
    """Test that invalid JSON in flags_json does not crash and returns empty list."""
    fin_risk = {"score": 50, "flags_json": "not json"}
    f = build_stock_facts("300085", "银之杰",
        signals=[], winrate={}, fin_risk=fin_risk,
        sector={"board_strength": None, "sector_rank": None, "theme_heat": []},
        holding=None, near_buy=None)
    assert f["risk_flags"]["has_data"] is True
    assert f["risk_flags"]["score"] == 50
    assert f["risk_flags"]["flags"] == []
