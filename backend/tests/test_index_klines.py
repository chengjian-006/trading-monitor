# -*- coding: utf-8 -*-
"""指数 5 分钟 K 线抓取解析 (v1.7.692) — 纯函数, 不连库不联网."""
import pytest

from backend.fetcher.index_klines import (
    INDEXES, SINA_MAX_DATALEN, IndexKlineError, parse_sina_klines,
)

_JSONP = (
    'var _k=(['
    '{"day":"2026-07-17 14:50:00","open":"3770.1","high":"3775.5","low":"3760.0",'
    '"close":"3766.2","volume":"1234500"},'
    '{"day":"2026-07-17 15:00:00","open":"3766.2","high":"3768.0","low":"3762.1",'
    '"close":"3764.1547","volume":"987600"}'
    ']);'
)


def test_parse_basic():
    bars = parse_sina_klines(_JSONP)
    assert len(bars) == 2
    assert bars[0]["dt"] == "2026-07-17 14:50:00"
    assert bars[-1]["close"] == pytest.approx(3764.1547)
    assert bars[-1]["volume"] == 987600
    assert isinstance(bars[-1]["volume"], int)


def test_parse_sorts_ascending():
    """新浪偶有乱序; 入库前必须升序, 否则增量判断和画图都会错。"""
    rev = ('([{"day":"2026-07-17 15:00:00","open":"1","high":"1","low":"1","close":"1","volume":"1"},'
           '{"day":"2026-07-17 09:35:00","open":"2","high":"2","low":"2","close":"2","volume":"2"}])')
    bars = parse_sina_klines(rev)
    assert [b["dt"] for b in bars] == ["2026-07-17 09:35:00", "2026-07-17 15:00:00"]


def test_parse_skips_dirty_rows_without_dropping_batch():
    """单根脏数据(缺字段/非数字)跳过即可, 不能让整批失败。"""
    txt = ('([{"day":"2026-07-17 09:35:00","open":"x","high":"1","low":"1","close":"1","volume":"1"},'
           '{"day":"2026-07-17 09:40:00","open":"1","high":"1","low":"1","close":"1","volume":"1"},'
           '{"nope":1}])')
    bars = parse_sina_klines(txt)
    assert len(bars) == 1 and bars[0]["dt"] == "2026-07-17 09:40:00"


def test_parse_empty_is_not_error():
    """非交易日新浪返回空数组 —— 是"没数据"不是"失败", 由调用方决定语义。"""
    assert parse_sina_klines("([])") == []
    assert parse_sina_klines("(null)") == []


def test_parse_garbage_raises():
    with pytest.raises(IndexKlineError):
        parse_sina_klines("<html>502 Bad Gateway</html>")


def test_index_codes_are_market_prefixed():
    """裸码会撞个股: kline_5m 里的 000001 实为平安银行, 000688=国城矿业, 000905=厦门港务。
    本表 code 必须带 sh/sz 前缀, 这是 0719 排查确认过的坑, 用测试钉死。"""
    codes = [c for c, _ in INDEXES]
    assert codes == ["sh000001", "sz399001", "sz399006"]
    for c in codes:
        assert c[:2] in ("sh", "sz"), f"{c} 缺市场前缀, 会与个股撞码"
        assert not c.isdigit()


def test_sina_datalen_cap_documented():
    """新浪硬上限 1023(实测), 常量别被随手改大而误以为能取更多历史。"""
    assert SINA_MAX_DATALEN == 1023
