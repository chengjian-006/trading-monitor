# backend/tests/test_bt_follow.py
"""bt_follow 纯函数单测: 涨停判定 / 跟风谓词 / 右侧快出族出场模拟. 不连库不联网."""
import numpy as np

from backend.scripts.bt_follow import is_limit_up, is_follower, exit_simulate


def test_limit_up_main_board():
    assert is_limit_up("600000", close=11.0, prev_close=10.0) is True
    assert is_limit_up("600000", close=10.9, prev_close=10.0) is False


def test_limit_up_chinext_20pct():
    assert is_limit_up("300001", close=12.0, prev_close=10.0) is True
    assert is_limit_up("300001", close=11.5, prev_close=10.0) is False


def test_follower_positive():
    assert is_follower(pct=0.03, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is True


def test_follower_rejects_too_strong():
    assert is_follower(pct=0.06, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is False


def test_follower_rejects_no_volume():
    assert is_follower(pct=0.03, vol=1000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.4, leader_pct=0.10) is False


def test_follower_rejects_weak_close():
    assert is_follower(pct=0.03, vol=2000, vol_ma10=1000,
                       high=10.5, low=10.0, close=10.1, leader_pct=0.10) is False


def test_exit_hits_target_then_runner():
    opens = np.array([10.0, 10.0, 10.6])
    highs = np.array([10.2, 10.8, 10.7])
    lows = np.array([9.9, 10.3, 8.9])
    closes = np.array([10.0, 10.6, 9.0])
    ma10 = np.array([np.nan, 10.5, 10.0])
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=3)
    assert status == "runner_ma10" and hit is True
    assert abs(ret - (-0.015)) < 1e-9


def test_exit_hard_stop():
    opens = np.array([10.0, 10.0])
    highs = np.array([10.1, 10.0])
    lows = np.array([9.3, 9.3])
    closes = np.array([10.0, 9.5])
    ma10 = np.array([np.nan, np.nan])
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=2)
    assert status == "stop_-6%" and hit is False
    assert abs(ret - (-0.06)) < 1e-9


def test_exit_time_stop_full():
    n = 12
    opens = np.full(n, 10.0); highs = np.full(n, 10.2)
    lows = np.full(n, 9.9); closes = np.full(n, 10.1)
    ma10 = np.full(n, 9.0)
    ret, hold, status, hit = exit_simulate(10.0, opens, highs, lows, closes, ma10, j=0, n=n)
    assert status == "to_full" and hit is False
    assert hold == 10
