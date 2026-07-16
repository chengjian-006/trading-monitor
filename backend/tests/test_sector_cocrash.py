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
    is_broken_down,
    pick_pool_hits,
)
from backend.services.sector_cocrash_guard import _in_window
from backend.services.industry_map_refresher import rows_from_wencai_df

from datetime import time as _t


class TestWindow:
    def test_in_window(self):
        assert _in_window(_t(9, 45)) is True
        assert _in_window(_t(11, 30)) is True
        assert _in_window(_t(15, 0)) is True

    def test_out_of_window(self):
        assert _in_window(_t(9, 44)) is False   # 开盘竞价噪音期不跑
        assert _in_window(_t(9, 30)) is False
        assert _in_window(_t(15, 1)) is False


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


class TestIsBrokenDown:
    """个股破位下跌闸: 距120日峰≤-15% + 收MA20下 + MA20拐头, 三条全中才算破位。"""

    def _rising_then_falling(self):
        # 前100根 100→199 上涨, 后60根 198→139 下跌(距峰约-30%)
        up = [100.0 + i for i in range(100)]
        down = [199.0 - i for i in range(1, 61)]
        return up + down

    def test_broken_true(self):
        closes = self._rising_then_falling()
        assert is_broken_down(closes, 136.0) is True   # 深跌+收均线下+均线拐头

    def test_strong_uptrend_false(self):
        closes = [100.0 + i * 0.5 for i in range(160)]
        assert is_broken_down(closes, 181.0) is False  # 一路涨, 现价创新高, 距峰≈0

    def test_shallow_pullback_false(self):
        # 涨到200只小回到188(距峰-6%), 未达-15%
        closes = [100.0 + i for i in range(100)] + [199.0 - i * 0.2 for i in range(1, 61)]
        assert is_broken_down(closes, 188.0) is False

    def test_deep_but_reclaimed_ma_false(self):
        # 深跌后强反弹, 现价站回MA20上方 → 不算破位
        closes = self._rising_then_falling()
        assert is_broken_down(closes, 175.0) is False  # 175 > 近期均线

    def test_insufficient_history_false(self):
        assert is_broken_down([100.0] * 50, 80.0) is False


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

    def test_broken_codes_filter(self):
        """传 broken_codes 时只保留破位票(均线上方的强势票即便跟跌也剔除)。"""
        pool = [{"code": "300390", "name": "天华新能"},
                {"code": "301292", "name": "海科新源"},
                {"code": "002192", "name": "融捷股份"}]
        qmap = {"300390": -15.8, "301292": -7.0, "002192": -10.0}
        hits = pick_pool_hits(pool, self.SECTORS, self.IND, qmap,
                              holding_codes=set(), broken_codes={"300390", "002192"})
        codes = {h["code"] for h in hits}
        assert codes == {"300390", "002192"}   # 301292 未破位被剔

    def test_broken_codes_none_keeps_all(self):
        """broken_codes=None(默认)不做破位过滤, 行业命中全留(向后兼容)。"""
        pool = [{"code": "300390", "name": "天华新能"}, {"code": "301292", "name": "海科新源"}]
        qmap = {"300390": -15.8, "301292": -7.0}
        hits = pick_pool_hits(pool, self.SECTORS, self.IND, qmap, holding_codes=set())
        assert {h["code"] for h in hits} == {"300390", "301292"}

    def test_no_quote_stock_skipped(self):
        pool = [{"code": "300390", "name": "天华新能"}]
        hits = pick_pool_hits(pool, self.SECTORS, self.IND, {}, holding_codes=set())
        assert hits == []


class TestCard:
    def test_build_card(self):
        """基线 v1.1 聚合卡形态: 归因行 + 全短列表(股票|跌幅|行业) + 👉建议 + 折叠 + 信封字段。"""
        sectors = {"电池化学品": {"down": 27, "total": 38, "ratio": 0.71}}
        hits = [{"code": "300390", "name": "天华新能", "pct": -15.8,
                 "industry": "电池化学品", "held": True},
                {"code": "301292", "name": "海科新源", "pct": -7.0,
                 "industry": "电池化学品", "held": False}]
        card = build_cocrash_card(0.052, sectors, hits)
        assert "禁补仓" in card.title and "自选2只" in card.title
        assert card.template == "orange"                       # 风险家族(谨慎档)橙 header
        assert card.tags == [("板块共振", "orange")]
        assert "禁补仓" in card.summary and "电池化学品" in card.summary
        assert card.subtitle                                    # 副标题(口径说明)非空
        # 归因行 + 全短列表 + 👉建议 + 折叠
        assert "**归因**" in card.elements[0]["content"]
        table_md = card.elements[1]["content"]
        assert "| 股票 | 跌幅 | 行业 |" in table_md
        assert "💼天华新能" in table_md                          # 持仓标记
        assert "<font color='green'>**-15.8%**</font>" in table_md   # 跌幅绿字加粗
        assert any("👉" in el.get("content", "") for el in card.elements)
        assert any(el.get("tag") == "collapsible_panel" for el in card.elements)
        # fallback 同源信息量
        assert "电池化学品" in card.fallback and "71%" in card.fallback
        assert "天华新能" in card.fallback and "👉" in card.fallback

    def test_sectors_ordered_by_ratio(self):
        sectors = {"电池化学品": {"down": 27, "total": 38, "ratio": 0.71},
                   "锂": {"down": 6, "total": 6, "ratio": 1.0}}
        hits = [{"code": "002192", "name": "融捷股份", "pct": -10.0, "industry": "锂", "held": False},
                {"code": "300390", "name": "天华新能", "pct": -15.8,
                 "industry": "电池化学品", "held": False}]
        card = build_cocrash_card(0.05, sectors, hits)
        # 归因行内行业按大跌占比降序: 锂(100%) 在 电池化学品(71%) 前
        assert card.fallback.index("锂") < card.fallback.index("电池化学品")


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
