"""指数急跌多指数判定测试 — _index_drop_pct / _worst_index_drop.

v1.7.385 追加两类数据层假象防护(0612误报普查):
1. 分时冻结回放: 分时源挂掉后反复回放同一份陈旧序列(科创0610/0611三天同值-1.16%假急跌)
   → 末点时间离 now 太远(按交易分钟算, 跨午休不误伤)就跳过该指数。
2. 竞价时段涨跌家数 0/0 假象: 降级源返回 上涨0/下跌0/跌停=全场, 原逻辑 up<=0→ratio=99
   把"无数据"当"极端恶化" → up==0 且 down==0 直接视为无数据, 涨跌家数恶化/跌停加速都不判。
"""
from backend.services import plunge_detector
from backend.services.plunge_detector import (
    _check_breadth,
    _check_speed,
    _index_drop_pct,
    _trading_minute,
    _worst_index_drop,
)


def _trends(prices):
    return [{"price": p} for p in prices]


CFG = {"PLUNGE_INDEX": {"enabled": True, "time_window_min": 10, "drop_threshold_pct": 1.0}}


def _idx(trends, pre_close):
    return {"trends": trends, "pre_close": pre_close}


class TestIndexDropPct:
    def test_too_few_bars(self):
        assert _index_drop_pct(_trends([10] * 5), 10) is None

    def test_drop_pct(self):
        tr = _trends([100] + [100] * 8 + [99, 98])
        assert round(_index_drop_pct(tr, 10), 2) == -2.0


class TestWorstIndexDrop:
    def test_none_breaches(self):
        flat = _idx(_trends([100] * 11), 100)
        out = _worst_index_drop(CFG, {"sh000001": flat, "sz399006": flat, "sh000688": flat})
        assert out is None

    def test_chuangye_breaches_when_shanghai_flat(self):
        flat = _idx(_trends([100] * 11), 100)
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 101)
        out = _worst_index_drop(CFG, {"sh000001": flat, "sz399006": cyb, "sh000688": flat})
        assert out is not None
        rule_id, name, parts = out
        assert rule_id == "PLUNGE_INDEX"
        assert "创业板指" in parts[0]

    def test_picks_worst_of_multiple(self):
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 100)
        kc = _idx(_trends([100] + [100] * 8 + [97, 95]), 100)
        out = _worst_index_drop(CFG, {"sh000001": _idx(_trends([100] * 11), 100),
                                      "sz399006": cyb, "sh000688": kc})
        assert "科创指数" in out[2][0]

    def test_disabled(self):
        cfg = {"PLUNGE_INDEX": {"enabled": False}}
        cyb = _idx(_trends([100] + [100] * 8 + [99, 98]), 100)
        assert _worst_index_drop(cfg, {"sz399006": cyb}) is None


def _timed_trends(prices, end_hhmm):
    """带时间的分时序列, 末点时间=end_hhmm, 往前每点回退1分钟."""
    eh, em = int(end_hhmm[:2]), int(end_hhmm[3:])
    end_abs = eh * 60 + em
    out = []
    for i, p in enumerate(prices):
        t = end_abs - (len(prices) - 1 - i)
        out.append({"time": f"{t // 60:02d}:{t % 60:02d}", "price": p})
    return out


DROP_PRICES = [100] + [100] * 8 + [99, 98]   # 10分钟窗口内跌2%, 破1%阈值


class TestTradingMinute:
    def test_morning(self):
        assert _trading_minute("09:30") == 0
        assert _trading_minute("10:05") == 35
        assert _trading_minute("11:30") == 120

    def test_lunch_clamped(self):
        assert _trading_minute("12:15") == 120

    def test_afternoon(self):
        assert _trading_minute("13:00") == 120
        assert _trading_minute("13:02") == 122
        assert _trading_minute("15:00") == 240

    def test_outside_session_clamped(self):
        assert _trading_minute("09:20") == 0
        assert _trading_minute("15:30") == 240


class TestStaleTrendsSkipped:
    def test_frozen_replay_skipped(self):
        # 末点停在 10:05、现在已 13:30 → 冻结回放, 即便跌幅破阈值也不触发
        kc = _idx(_timed_trends(DROP_PRICES, "10:05"), 100)
        assert _worst_index_drop(CFG, {"sh000688": kc}, now_hhmm="13:30") is None

    def test_full_day_replay_skipped(self):
        # 回放的是前一天全天序列(末点15:00), 现在是早盘 → 同样陈旧
        kc = _idx(_timed_trends(DROP_PRICES, "15:00"), 100)
        assert _worst_index_drop(CFG, {"sh000688": kc}, now_hhmm="09:45") is None

    def test_fresh_data_still_fires(self):
        kc = _idx(_timed_trends(DROP_PRICES, "10:05"), 100)
        out = _worst_index_drop(CFG, {"sh000688": kc}, now_hhmm="10:06")
        assert out is not None and "科创指数" in out[2][0]

    def test_lunch_boundary_not_stale(self):
        # 末点11:30、现在13:02 → 按交易分钟只差2分钟, 不算陈旧
        kc = _idx(_timed_trends(DROP_PRICES, "11:30"), 100)
        assert _worst_index_drop(CFG, {"sh000688": kc}, now_hhmm="13:02") is not None

    def test_no_time_field_backward_compat(self):
        # 无 time 字段的旧格式无法判陈旧 → 不拦(维持原行为)
        kc = _idx(_trends(DROP_PRICES), 100)
        assert _worst_index_drop(CFG, {"sh000688": kc}, now_hhmm="13:30") is not None

    def test_stale_index_skipped_but_fresh_one_fires(self):
        stale = _idx(_timed_trends([100] + [100] * 8 + [97, 95], "10:00"), 100)
        fresh = _idx(_timed_trends(DROP_PRICES, "14:00"), 100)
        out = _worst_index_drop(CFG, {"sh000688": stale, "sz399006": fresh}, now_hhmm="14:01")
        assert out is not None and "创业板指" in out[2][0]


BREADTH_CFG = {"PLUNGE_BREADTH": {"enabled": True, "down_up_ratio": 3.0, "drop_gt3_pct": 25.0}}
SPEED_CFG = {"PLUNGE_SPEED": {"enabled": True, "new_limit_down": 8, "time_window_min": 5}}


class TestBreadthAuctionGuard:
    def test_zero_zero_is_no_data_not_extreme(self):
        # 竞价时段降级源: 上涨0/下跌0/跌停=全场 → 无数据, 不是恶化
        stats = {"up_count": 0, "down_count": 0, "limit_down": 5523}
        assert _check_breadth(BREADTH_CFG, stats) is None

    def test_real_deterioration_still_fires(self):
        stats = {"up_count": 500, "down_count": 4500, "limit_down": 60}
        out = _check_breadth(BREADTH_CFG, stats)
        assert out is not None and out[0] == "PLUNGE_BREADTH"


class TestSpeedAuctionGuard:
    def setup_method(self):
        plunge_detector._prev_limit_down = None
        plunge_detector._prev_limit_down_time = None

    def test_zero_zero_skipped_and_prev_not_poisoned(self):
        stats = {"up_count": 0, "down_count": 0, "limit_down": 5523}
        assert _check_speed(SPEED_CFG, stats) is None
        assert plunge_detector._prev_limit_down is None

    def test_midday_degrade_does_not_fire(self):
        # 盘中源降级: 真实基线50家 → 假5523家, 不该算"新增跌停5473家"
        plunge_detector._prev_limit_down = 50
        plunge_detector._prev_limit_down_time = "10:00"
        stats = {"up_count": 0, "down_count": 0, "limit_down": 5523}
        assert _check_speed(SPEED_CFG, stats) is None
        assert plunge_detector._prev_limit_down == 50
