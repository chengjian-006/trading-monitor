"""自选池昨日广度纯函数测试 — stocks._compute_pool_breadth.

锁住: pct 现算(收盘 vs 前一根)、涨跌平计数、历史日涨跌停判定(主板/创业板阈值)、
缺 target 或缺前一根记 no_data、均值与红盘率。
"""
from backend.routers.stocks import _compute_pool_breadth


def _bars(*rows):
    return [{"trade_date": d, "close": c} for d, c in rows]


def test_up_down_flat_and_limit_up():
    codes = ["600000", "000001", "300750"]
    bars = {
        "600000": _bars(("2026-03-05", 10.0), ("2026-03-06", 11.0)),    # +10% 主板涨停
        "000001": _bars(("2026-03-05", 10.0), ("2026-03-06", 9.5)),     # -5%  跌
        "300750": _bars(("2026-03-05", 100.0), ("2026-03-06", 100.0)),  # 0    平
    }
    names = {"600000": "浦发银行", "000001": "平安银行", "300750": "宁德时代"}
    r = _compute_pool_breadth(codes, bars, names, "2026-03-06")
    assert r["total"] == 3
    assert (r["up"], r["down"], r["flat"]) == (1, 1, 1)
    assert r["limit_up"] == 1 and r["limit_down"] == 0    # 600000 +10% 主板涨停
    assert r["avg"] == round((10.0 - 5.0 + 0.0) / 3, 2)
    assert r["up_ratio"] == round(1 / 3 * 100)
    assert r["trade_date"] == "2026-03-06" and r["no_data"] == 0


def test_limit_down_chuangye():
    r = _compute_pool_breadth(["300750"],
                              {"300750": _bars(("2026-03-05", 100.0), ("2026-03-06", 80.0))},  # -20% 创业板跌停
                              {"300750": "宁德时代"}, "2026-03-06")
    assert r["limit_down"] == 1 and r["down"] == 1 and r["limit_up"] == 0


def test_no_data_when_code_missing_or_no_prev_bar():
    codes = ["600000", "999999", "600519"]
    bars = {
        "600000": _bars(("2026-03-05", 10.0), ("2026-03-06", 10.5)),   # 有数据
        # 999999 完全缺 bar
        "600519": _bars(("2026-03-06", 1600.0)),                        # 只有 target, 无前一根
    }
    r = _compute_pool_breadth(codes, bars, {}, "2026-03-06")
    assert r["total"] == 1 and r["no_data"] == 2


def test_empty_pool_returns_zeros():
    r = _compute_pool_breadth([], {}, {}, "2026-03-06")
    assert r["total"] == 0 and r["avg"] is None and r["up_ratio"] is None
