"""成本类硬止损"确认延迟"护栏单测 (v1.7.421).

_stop_confirm_ok: 硬止损(SELL_LOSS_10/SELL_WEAK_STOP)首次碰线挂起, 连续碰线满
confirm_sec 才放行; 收复(碰线间隔 > GAP_RESET)自动重置 — 防早盘下影线插针误推。
纯内存 + 注入 now, 不连库不打网。
"""
import pytest

from backend.services import scanner
from backend.services.scanner import _stop_confirm_ok


@pytest.fixture(autouse=True)
def _clear_state():
    scanner._stop_confirm_state.clear()
    yield
    scanner._stop_confirm_state.clear()


def test_disabled_passes_immediately():
    """confirm_sec=0 → 立即放行(关闭护栏)."""
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 0, now=1000.0) is True


def test_first_touch_held_then_confirmed():
    """首次碰线挂起(False); 连续碰线(间隔≤GAP_RESET)累计满 confirm_sec 才放行(True)."""
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 300, now=1000.0) is False  # 首次
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 300, now=1100.0) is False  # 累计100<300
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 300, now=1200.0) is False  # 累计200<300
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 300, now=1300.0) is True   # 累计300 确认


def test_wick_recovers_before_confirm_never_passes():
    """插针: 碰线一次后收复(下次碰线已隔 >GAP_RESET) → 视为新一轮, 仍 False, 从不放行."""
    assert _stop_confirm_ok(1, "002463", "SELL_LOSS_10", 300, now=1000.0) is False  # 09:35 插针
    # 收复 ~5min 后再次碰线(间隔 300s > 120s GAP_RESET) → 新一轮重新计时, 不会因旧起点误确认
    assert _stop_confirm_ok(1, "002463", "SELL_LOSS_10", 300, now=1300.0) is False


def test_continuous_touch_small_gaps_accumulate():
    """连续碰线(间隔 < GAP_RESET)累计计时, 满阈值放行."""
    assert _stop_confirm_ok(1, "300274", "SELL_WEAK_STOP", 300, now=2000.0) is False
    assert _stop_confirm_ok(1, "300274", "SELL_WEAK_STOP", 300, now=2060.0) is False  # +60s
    assert _stop_confirm_ok(1, "300274", "SELL_WEAK_STOP", 300, now=2160.0) is False  # +100s 累计160
    assert _stop_confirm_ok(1, "300274", "SELL_WEAK_STOP", 300, now=2260.0) is False  # 累计260<300
    assert _stop_confirm_ok(1, "300274", "SELL_WEAK_STOP", 300, now=2305.0) is True   # 累计305 确认


def test_keys_isolated_per_user_code_signal():
    """不同 (user,code,sig) 互不干扰."""
    assert _stop_confirm_ok(1, "000725", "SELL_LOSS_10", 300, now=5000.0) is False
    assert _stop_confirm_ok(2, "000725", "SELL_LOSS_10", 300, now=5000.0) is False  # 另一 user 独立
    assert _stop_confirm_ok(1, "000725", "SELL_WEAK_STOP", 300, now=5000.0) is False  # 另一 sig 独立


def test_break_ma_signals_guarded():
    """v1.7.425 方案A: 跌破MA5/10/20 纳入确认延迟守护集 — 防开盘插针误推(京东方A 0616案例:
    09:33 瞬时-0.84%擦破MA10即报, 09:35即收复全天收+4.22%)."""
    for sig in ("SELL_BREAK_MA5", "SELL_BREAK_MA10", "SELL_BREAK_MA20"):
        assert sig in scanner._STOP_CONFIRM_GUARDED


def test_break_ma10_wick_held_then_confirmed():
    """跌破MA10 复用同一确认延迟机制: 首次擦破挂起, 持续满 confirm_sec 才放行."""
    assert _stop_confirm_ok(1, "000725", "SELL_BREAK_MA10", 300, now=1000.0) is False  # 09:33 擦破挂起
    assert _stop_confirm_ok(1, "000725", "SELL_BREAK_MA10", 300, now=1100.0) is False  # 累计100<300
    assert _stop_confirm_ok(1, "000725", "SELL_BREAK_MA10", 300, now=1200.0) is False  # 累计200<300
    assert _stop_confirm_ok(1, "000725", "SELL_BREAK_MA10", 300, now=1300.0) is True   # 累计300 确认
