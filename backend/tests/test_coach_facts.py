from backend.services.ai_advisor.coach_facts import build_coach_facts


def _r(pnl_pct, hold, model=None, dev=None, status="closed", pnl=0.0):
    return {"realized_pnl_pct": pnl_pct, "holding_days": hold, "entry_model_name": model,
            "entry_deviation_pct": dev, "exit_reason": None, "status": status,
            "close_date": "2026-07-10", "realized_pnl": pnl}


def test_listen_vs_self_split_and_winrate():
    rounds = [
        _r(5.0, 3, model="回踩MA10"), _r(-2.0, 8, model="回踩MA10"),   # 听模型: 1胜1负=50%
        _r(-4.0, 12, model=None), _r(-1.0, 20, model=None), _r(3.0, 2, model=None),  # 自作主张: 1胜2负≈33%
    ]
    f = build_coach_facts(rounds, {}, "2026-06-01", "2026-07-10")
    assert f["listen_vs_self"]["listen"]["n"] == 2
    assert f["listen_vs_self"]["listen"]["win_rate"] == 50.0
    assert f["listen_vs_self"]["self"]["n"] == 3
    assert round(f["listen_vs_self"]["self"]["win_rate"], 1) == 33.3


def test_by_model_exec_gap_vs_market():
    rounds = [_r(6.0, 3, model="缩量突破"), _r(4.0, 4, model="缩量突破")]  # 实盘100%
    winrate = {"BUY_VOL_BREAKOUT": {"model_name": "缩量突破", "win_rate_3m": 65.0, "n_3m": 200}}
    f = build_coach_facts(rounds, winrate, "2026-06-01", "2026-07-10")
    m = next(x for x in f["by_model"] if x["model_name"] == "缩量突破")
    assert m["n"] == 2 and m["win_rate"] == 100.0
    assert m["market_win_rate_3m"] == 65.0
    assert m["exec_gap"] == 35.0    # 实盘胜率 - 全市场回测胜率


def test_winner_vs_loser_hold_days():
    rounds = [_r(10.0, 2), _r(8.0, 3), _r(-5.0, 15), _r(-3.0, 25)]
    f = build_coach_facts(rounds, {}, "s", "e")
    assert f["cycle"]["winner_hold_avg"] == 2.5   # 赢家平均持 (2+3)/2
    assert f["cycle"]["loser_hold_avg"] == 20.0   # 输家平均扛 (15+25)/2


def test_open_rounds_excluded_from_closed_stats():
    rounds = [_r(5.0, 3, status="closed"), _r(0.0, 0, status="open")]
    f = build_coach_facts(rounds, {}, "s", "e")
    assert f["n_closed"] == 1


def test_empty_rounds_safe():
    f = build_coach_facts([], {}, "s", "e")
    assert f["n_closed"] == 0 and f["by_model"] == []
