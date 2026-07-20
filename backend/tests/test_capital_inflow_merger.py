"""资金回流·板块预警 merger 测试.

覆盖:
  - 单板块详细格式
  - 同龙头多板块联名合并 (煤炭/煤炭开采 共享昊华能源)
  - 不同龙头多组列表格式
  - 涨幅区间格式化 (相等→单值, 不等→范围)
  - 关注个股按行去重并集
"""
from backend.services.capital_inflow_scanner import (
    _merge_capital_inflow,
    _group_by_leader,
    _fmt_pct_range,
    _first_num,
)


def _mk(sector_name, sector_pct, leader_code, leader_name, leader_pct=10.0,
        sector_top_avg=5.0, sector_top_n=10, stock_lines=None, my_stocks_count=0):
    return {
        "sector_name": sector_name,
        "sector_pct": sector_pct,
        "leader_code": leader_code,
        "leader_name": leader_name,
        "leader_pct": leader_pct,
        "sector_top_n": sector_top_n,
        "sector_top_avg": sector_top_avg,
        "stock_lines": stock_lines or [],
        "my_stocks_count": my_stocks_count,
    }


class TestFmtPctRange:
    def test_equal_collapses_to_single(self):
        assert _fmt_pct_range(3.14, 3.14) == "+3.14%"

    def test_near_equal_within_threshold_collapses(self):
        assert _fmt_pct_range(3.14, 3.149) == "+3.14%"

    def test_range_format(self):
        assert _fmt_pct_range(2.86, 3.14) == "+2.86~+3.14%"

    def test_negative_range(self):
        assert _fmt_pct_range(-3.5, -1.2) == "-3.50~-1.20%"


class TestGroupByLeader:
    def test_single_item_passthrough(self):
        items = [_mk("煤炭", 3.14, "601101", "昊华能源")]
        out = _group_by_leader(items)
        assert len(out) == 1
        assert out[0]["sector_name"] == "煤炭"
        # 单条不应有 _merged_count
        assert "_merged_count" not in out[0]

    def test_same_leader_two_sectors_merged(self):
        items = [
            _mk("煤炭", 3.14, "601101", "昊华能源", sector_top_avg=5.53),
            _mk("煤炭开采", 2.86, "601101", "昊华能源", sector_top_avg=5.21),
        ]
        out = _group_by_leader(items)
        assert len(out) == 1
        g = out[0]
        assert g["sector_name"] == "煤炭 / 煤炭开采"
        assert g["sector_pct_range"] == (2.86, 3.14)
        assert g["sector_top_avg_range"] == (5.21, 5.53)
        assert g["_merged_count"] == 2

    def test_different_leaders_kept_separate(self):
        items = [
            _mk("煤炭", 3.14, "601101", "昊华能源"),
            _mk("电池", 1.53, "300750", "宁德时代"),
        ]
        out = _group_by_leader(items)
        assert len(out) == 2

    def test_stock_lines_union_dedup(self):
        # 两个板块都列了用户股 A, 一个还列了 B → 合并后 [A, B] (按行字符串去重保序)
        items = [
            _mk("煤炭", 3.14, "601101", "昊华能源",
                stock_lines=["  • 中国神华(601088)  ...", "  • 兖矿能源(600188)  ..."],
                my_stocks_count=2),
            _mk("煤炭开采", 2.86, "601101", "昊华能源",
                stock_lines=["  • 中国神华(601088)  ...", "  • 山西焦煤(000983)  ..."],
                my_stocks_count=2),
        ]
        out = _group_by_leader(items)
        assert len(out) == 1
        # 3 只不重复 (中国神华 / 兖矿能源 / 山西焦煤)
        assert out[0]["my_stocks_count"] == 3
        assert len(out[0]["stock_lines"]) == 3


class TestMergeCapitalInflow:
    def test_empty_returns_empty_string(self):
        assert _merge_capital_inflow([]) == ""

    def test_single_item_detailed_format(self):
        items = [_mk("电池", 1.53, "300750", "宁德时代", leader_pct=9.98)]
        text = _merge_capital_inflow(items)
        assert "【资金回流·板块预警】" in text
        assert "电池" in text
        assert "宁德时代" in text
        assert "+1.53%" in text
        assert "(你的股票池中暂无该板块个股)" in text

    def test_same_leader_two_sectors_single_block(self):
        items = [
            _mk("煤炭", 3.14, "601101", "昊华能源", sector_top_avg=5.53),
            _mk("煤炭开采", 2.86, "601101", "昊华能源", sector_top_avg=5.21),
        ]
        text = _merge_capital_inflow(items)
        # 合并后只有 1 组 → 详细格式 (无"近15分钟 N 波"头)
        assert "波资金共振" not in text
        assert "煤炭 / 煤炭开采" in text
        assert "+2.86~+3.14%" in text
        assert "+5.21~+5.53%" in text

    def test_multiple_leaders_list_format(self):
        items = [
            _mk("煤炭", 3.14, "601101", "昊华能源"),
            _mk("电池", 1.53, "300750", "宁德时代"),
            _mk("光伏", 2.10, "300274", "阳光电源"),
        ]
        text = _merge_capital_inflow(items)
        assert "近15分钟 3 波资金共振" in text
        # 3 个 ▸ 块
        assert text.count("▸") == 3

    def test_stock_lines_listed_in_single_group(self):
        items = [_mk("电池", 1.53, "300750", "宁德时代",
                     stock_lines=["  • 宁德时代(300750)  价格 198.55  涨幅 +9.90%  成交 12.34亿"],
                     my_stocks_count=1)]
        text = _merge_capital_inflow(items)
        assert "你自选的该板块个股 (1只)" in text
        assert "宁德时代(300750)" in text


class TestFirstNum:
    """v1.7.722: `_first_num` 取代 `dict.get(k, fallback)` 的回退写法。

    0720 事故: 09:23 集合竞价时段推出"半导体 自选票22只"卡, 22 只全是 +0.00% / 成交-。
    成因之一就是 `rt.get("pct_change", t.get("pct_change", 0))` —— dict.get 只在【键不存在】
    时取默认值, 而行情源返回了这只票、只是 pct_change 为 0/None 时键是存在的, 于是
    "回退到板块榜涨幅"的意图永远不生效。这组用例把该回归钉死。
    """

    def test_takes_first_valid(self):
        assert _first_num(3.21, 9.9) == 3.21

    def test_falls_back_when_primary_is_zero(self):
        # 病根场景: 主源键存在但值为 0 → 必须继续回退, 不能直接返回 0
        assert _first_num(0, 1.48) == 1.48

    def test_falls_back_when_primary_is_none(self):
        assert _first_num(None, 1.48) == 1.48

    def test_all_missing_returns_zero(self):
        assert _first_num(None, 0, None) == 0.0

    def test_ignores_non_numeric(self):
        assert _first_num("abc", None, 2.5) == 2.5

    def test_negative_is_valid(self):
        # 跌幅是有效值, 不能被当成缺失continue掉
        assert _first_num(-3.4, 9.9) == -3.4

    def test_numeric_string_accepted(self):
        assert _first_num("1.48") == 1.48
