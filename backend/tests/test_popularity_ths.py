"""同花顺人气榜切换单测 (v1.7.x).

验证: 热榜 JSON 解析(去重/A股过滤/top_n/字段映射) + 逐票批量(命中名次/落榜标100名外/整榜失败回空) + 展示格式化.
纯函数 + monkeypatch 桩, 不连库不打网.
"""
import asyncio

from backend.fetcher import popularity as pop
from backend.fetcher.popularity import RANK_OUT_OF_TOP100


_SAMPLE = {
    "status_code": 0,
    "data": {
        "stock_list": [
            {"code": "601991", "name": "大唐发电", "order": 1, "hot_rank_chg": 0,
             "rate": "6745719.0", "rise_and_fall": 4.94},
            {"code": "600487", "name": "亨通光电", "order": 2, "hot_rank_chg": 3,
             "rate": "6171220.0", "rise_and_fall": 3.25},
            {"code": "603986", "name": "兆易创新", "order": 3, "hot_rank_chg": -5,
             "rate": "4938140.0", "rise_and_fall": 10.0},
            {"code": "601991", "name": "大唐发电", "order": 99, "hot_rank_chg": 0,
             "rate": "1.0", "rise_and_fall": 0},          # 重复 code, 应去重
            {"code": "00700", "name": "腾讯控股", "order": 4, "hot_rank_chg": 0,
             "rate": "1.0", "rise_and_fall": 0},          # 非6位 A股, 应过滤
            {"code": "002475", "name": "立讯精密", "order": 5, "hot_rank_chg": 1,
             "rate": "100.0", "rise_and_fall": -1.2},
        ]
    },
}


def test_parse_basic_fields():
    out = pop._parse_ths_hot_list(_SAMPLE, 100)
    assert [s["code"] for s in out] == ["601991", "600487", "603986", "002475"]
    first = out[0]
    assert first["rank"] == 1
    assert first["rank_change"] == 0
    assert first["name"] == "大唐发电"
    assert first["heat"] == 6745719.0
    assert out[1]["rank_change"] == 3
    assert out[2]["rank_change"] == -5     # 正=上升 / 负=下降, 与前端 ↑↓ 对齐


def test_parse_dedup_and_a_share_filter():
    out = pop._parse_ths_hot_list(_SAMPLE, 100)
    codes = [s["code"] for s in out]
    assert codes.count("601991") == 1      # 去重
    assert "00700" not in codes            # 非6位数字过滤


def test_parse_top_n_limit():
    out = pop._parse_ths_hot_list(_SAMPLE, 2)
    assert len(out) == 2
    assert [s["code"] for s in out] == ["601991", "600487"]


def test_parse_empty_or_garbage():
    assert pop._parse_ths_hot_list({}, 10) == []
    assert pop._parse_ths_hot_list({"data": {}}, 10) == []
    assert pop._parse_ths_hot_list(None, 10) == []
    assert pop._parse_ths_hot_list({"data": {"stock_list": [None, "x", {}]}}, 10) == []


def test_for_codes_hit_and_out(monkeypatch):
    async def fake_rank(top_n):
        return [{"code": "601991", "rank": 1}, {"code": "002475", "rank": 5}]
    monkeypatch.setattr(pop, "get_popularity_rank", fake_rank)

    res = asyncio.run(pop.get_popularity_rank_for_codes(["601991", "300999", "002475"]))
    assert res["601991"] == 1
    assert res["002475"] == 5
    assert res["300999"] == RANK_OUT_OF_TOP100      # 落榜 → 100名外哨兵
    assert set(res.keys()) == {"601991", "300999", "002475"}   # 不遗漏任何请求 code


def test_for_codes_fetch_fail_returns_empty(monkeypatch):
    async def fake_empty(top_n):
        return []
    monkeypatch.setattr(pop, "get_popularity_rank", fake_empty)
    res = asyncio.run(pop.get_popularity_rank_for_codes(["601991", "300999"]))
    assert res == {}        # 整榜失败不写哨兵, 避免误标100名外


def test_fmt_pop_rank():
    assert pop.fmt_pop_rank(1) == "第1"
    assert pop.fmt_pop_rank(100) == "第100"
    assert pop.fmt_pop_rank(101) == "100名外"
    assert pop.fmt_pop_rank(RANK_OUT_OF_TOP100) == "100名外"
    assert pop.fmt_pop_rank(None) == ""
    assert pop.fmt_pop_rank("bad") == ""
