"""全市场广度纯函数测试 — _breadth_from_closes 口径锁定."""
from backend.services.market_breadth_refresher import _breadth_from_closes


def _seq(n, val):
    return [float(val)] * n


class TestBreadthFromCloses:
    def test_empty(self):
        assert _breadth_from_closes([]) == {
            "ma20_ratio": 0.0, "ma10_ratio": 0.0, "ma60_ratio": 0.0, "total": 0}

    def test_too_short_excluded(self):
        out = _breadth_from_closes([_seq(19, 10)])
        assert out["total"] == 0

    def test_all_above_all_mas(self):
        closes = _seq(60, 10) + [100.0]
        out = _breadth_from_closes([closes])
        assert out["total"] == 1
        assert out["ma20_ratio"] == 100.0
        assert out["ma10_ratio"] == 100.0
        assert out["ma60_ratio"] == 100.0

    def test_below_all_mas(self):
        closes = _seq(60, 100) + [1.0]
        out = _breadth_from_closes([closes])
        assert out["total"] == 1
        assert out["ma20_ratio"] == 0.0
        assert out["ma10_ratio"] == 0.0
        assert out["ma60_ratio"] == 0.0

    def test_mixed_ratio_and_ma60_sample_subset(self):
        a = _seq(60, 10) + [100.0]
        b = _seq(25, 10) + [100.0]
        out = _breadth_from_closes([a, b])
        assert out["total"] == 2
        assert out["ma20_ratio"] == 100.0
        assert out["ma60_ratio"] == 50.0
