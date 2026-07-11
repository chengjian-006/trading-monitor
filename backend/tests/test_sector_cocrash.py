# -*- coding: utf-8 -*-
"""板块共振·禁补仓提示 (v1.7.598) — 纯函数单测。

回测背书(bt_avoid_rules2, 2023~2026-07 IS/OOS一致): 破位大跌日抄底按语境分化 —
大盘恐慌日(全市场大跌占比>=10%)抄底历史为正(不发卡);
板块共振跌(大盘正常+行业超额大跌>=20pp)是最差语境(T+5~T+20期望为负, 日胜率33%~47%) → 发禁补仓卡。
"""
import pandas as pd

from backend.services.sector_cocrash_guard import (
    build_cocrash_card,
    detect_cocrash_sectors,
    pick_pool_hits,
)
from backend.services.industry_map_refresher import rows_from_wencai_df


def _quotes(spec: list[tuple[str, float]]) -> list[dict]:
    return [{"code": c, "pct": p} for c, p in spec]


def _make_market(n_flat: int = 90, start: int = 0) -> list[tuple[str, float]]:
    """n_flat 只平盘票(行业'其他'), 代码从 600000+start 开始。"""
    return [(f"{600000 + start + i}", 0.0) for i in range(n_flat)]


class TestDetect:
    def test_sector_cocrash_triggers(self):
        """大盘正常(8%) + 锂行业 8/10 大跌(超额72pp) → 触发。"""
        spec = _make_market(90)
        spec += [(f"{300000 + i}", -6.0 if i < 8 else 0.0) for i in range(10)]
        ind = {c: ("锂" if c.startswith("300") else "其他") for c, _ in spec}
        res = detect_cocrash_sectors(_quotes(spec), ind)
        assert res["panic"] < 0.10
        assert "锂" in res["sectors"]
        s = res["sectors"]["锂"]
        assert s["down"] == 8 and s["total"] == 10
        assert s["ratio"] == 0.8

    def test_panic_day_suppressed(self):
        """大盘恐慌日(>=10%全市场大跌)不发 — 回测该语境抄底为正, 禁抄闸不适用。"""
        spec = [(f"{600000 + i}", -6.0 if i < 30 else 0.0) for i in range(90)]
        spec += [(f"{300000 + i}", -9.0) for i in range(10)]     # 锂 100% 大跌
        ind = {c: ("锂" if c.startswith("300") else "其他") for c, _ in spec}
        res = detect_cocrash_sectors(_quotes(spec), ind)
        assert res["panic"] >= 0.10
        assert res["sectors"] == {}

    def test_small_industry_not_triggered(self):
        """成员<8 的行业不触发(样本太小, 两三只跌停就100%)。"""
        spec = _make_market(95)
        spec += [(f"{300000 + i}", -9.0) for i in range(5)]
        ind = {c: ("锂" if c.startswith("300") else "其他") for c, _ in spec}
        res = detect_cocrash_sectors(_quotes(spec), ind)
        assert res["sectors"] == {}

    def test_insufficient_excess_not_triggered(self):
        """行业超额不足20pp不触发(行业25% vs 大盘8%, 超额17pp)。"""
        spec = [(f"{600000 + i}", -6.0 if i < 8 else 0.0) for i in range(80)]
        spec += [(f"{300000 + i}", -6.0 if i < 5 else 0.0) for i in range(20)]  # 行业25%
        ind = {c: ("锂" if c.startswith("300") else "其他") for c, _ in spec}
        res = detect_cocrash_sectors(_quotes(spec), ind)
        assert "锂" not in res["sectors"]

    def test_unmapped_codes_ignored_for_sector(self):
        """无行业映射的票只进大盘统计, 不产生行业分组。"""
        spec = _make_market(90) + [(f"{300000 + i}", -9.0) for i in range(10)]
        ind = {c: "其他" for c, _ in spec if c.startswith("600")}   # 锂票全无映射
        res = detect_cocrash_sectors(_quotes(spec), ind)
        assert res["sectors"] == {}


class TestPoolHits:
    IND = {"300390": "电池化学品", "301292": "电池化学品", "002192": "锂", "600519": "白酒"}
    SECTORS = {"电池化学品": {"down": 27, "total": 38, "ratio": 0.71},
               "锂": {"down": 6, "total": 6, "ratio": 1.0}}

    def test_pick_and_sort(self):
        pool = [{"code": "300390", "name": "天华新能"},
                {"code": "301292", "name": "海科新源"},
                {"code": "600519", "name": "贵州茅台"},     # 行业未触发 → 不入
                {"code": "002192", "name": "融捷股份"}]
        qmap = {"300390": -15.8, "301292": -7.0, "002192": -10.0, "600519": -1.0}
        hits = pick_pool_hits(pool, self.SECTORS, self.IND, qmap, holding_codes={"300390"})
        codes = [h["code"] for h in hits]
        assert "600519" not in codes
        assert codes[0] == "300390"          # 跌最深在前
        assert hits[0]["held"] is True and hits[1]["held"] is False

    def test_no_quote_stock_skipped(self):
        pool = [{"code": "300390", "name": "天华新能"}]
        hits = pick_pool_hits(pool, self.SECTORS, self.IND, {}, holding_codes=set())
        assert hits == []


class TestCard:
    def test_build_card(self):
        sectors = {"电池化学品": {"down": 27, "total": 38, "ratio": 0.71}}
        hits = [{"code": "300390", "name": "天华新能", "pct": -15.8,
                 "industry": "电池化学品", "held": True},
                {"code": "301292", "name": "海科新源", "pct": -7.0,
                 "industry": "电池化学品", "held": False}]
        title, body = build_cocrash_card(0.052, sectors, hits)
        assert "禁补仓" in title
        assert "电池化学品" in body and "71%" in body and "27/38" in body
        assert "全市场 5.2%" in body
        assert "💼" in body and "天华新能(300390)" in body and "-15.8%" in body
        assert "海科新源(301292)" in body
        assert "期望为负" in body and "恐慌普跌日" in body

    def test_sectors_ordered_by_ratio(self):
        sectors = {"电池化学品": {"down": 27, "total": 38, "ratio": 0.71},
                   "锂": {"down": 6, "total": 6, "ratio": 1.0}}
        hits = [{"code": "002192", "name": "融捷股份", "pct": -10.0, "industry": "锂", "held": False},
                {"code": "300390", "name": "天华新能", "pct": -15.8,
                 "industry": "电池化学品", "held": False}]
        _, body = build_cocrash_card(0.05, sectors, hits)
        assert body.index("锂") < body.index("电池化学品")   # 占比高的行业在前


class TestWencaiParse:
    def test_rows_from_df(self):
        df = pd.DataFrame([
            {"股票代码": "300390.SZ", "所属同花顺行业": "电力设备-电池-电池化学品", "code": "300390"},
            {"股票代码": "002192.SZ", "所属同花顺行业": "有色金属-能源金属-锂", "code": "002192"},
            {"股票代码": "920222.BJ", "所属同花顺行业": "机械", "code": None},   # code 空时取股票代码前6位
            {"股票代码": "600000.SH", "所属同花顺行业": None, "code": "600000"},  # 无行业 → 跳过
        ])
        rows = rows_from_wencai_df(df)
        assert ("300390", "电力设备-电池-电池化学品") in rows
        assert ("002192", "有色金属-能源金属-锂") in rows
        assert ("920222", "机械") in rows
        assert all(c != "600000" for c, _ in rows)

    def test_empty_df(self):
        assert rows_from_wencai_df(None) == []
        assert rows_from_wencai_df(pd.DataFrame()) == []
