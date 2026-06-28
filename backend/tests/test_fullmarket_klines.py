"""全市场日线 纯函数测试 — 解析/过滤/续跑判定."""
from backend.services.fullmarket_klines import (
    _parse_sina_klines, _filter_symbols, _needs_backfill,
)


class TestParseSinaKlines:
    def test_empty_text(self):
        assert _parse_sina_klines("") == []

    def test_no_parens(self):
        assert _parse_sina_klines("garbage no json") == []

    def test_valid_jsonp(self):
        text = ('jsonp([{"day":"2026-06-04","open":"10.0","high":"11.0",'
                '"low":"9.5","close":"10.5","volume":"1000"}])')
        rows = _parse_sina_klines(text)
        assert rows == [("2026-06-04", 10.0, 11.0, 9.5, 10.5, 1000.0)]

    def test_skips_malformed_entry(self):
        text = ('cb([{"day":"2026-06-04","open":"10","high":"11","low":"9",'
                '"close":"10.5","volume":"1000"},{"day":"2026-06-05"}])')
        rows = _parse_sina_klines(text)
        assert len(rows) == 1
        assert rows[0][0] == "2026-06-04"

    def test_empty_array(self):
        assert _parse_sina_klines("cb([])") == []


class TestFilterSymbols:
    def test_keeps_sh_sz_drops_bj_st_delisted(self):
        rows = [
            {"symbol": "sh600519", "name": "贵州茅台"},
            {"symbol": "sz000001", "name": "平安银行"},
            {"symbol": "bj830799", "name": "艾融软件"},
            {"symbol": "sz000004", "name": "ST国华"},
            {"symbol": "sh600891", "name": "退市秋林"},
            {"symbol": "sz000005", "name": "*ST星源"},
        ]
        assert _filter_symbols(rows) == ["sh600519", "sz000001"]

    def test_empty(self):
        assert _filter_symbols([]) == []


class TestNeedsBackfill:
    def test_below_threshold(self):
        assert _needs_backfill(0, 1000) is True
        assert _needs_backfill(500, 1000) is True

    def test_at_or_above_threshold(self):
        assert _needs_backfill(1000, 1000) is False
        assert _needs_backfill(1300, 1000) is False
