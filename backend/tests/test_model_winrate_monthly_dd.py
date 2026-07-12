"""图鉴回撤+逐月胜率 纯函数测试 (功能10, v1.7.x).

_monthly_series: 近6月交易 [(date,ret)] → 逐月 [{ym,win_rate,n,net}] 升序, 空月不补。
_max_drawdown: 按日排序累计等权净收益曲线, 峰到谷最大回撤(百分点正数); 样本<5→None。
"""
from backend.services.model_winrate_refresher import _monthly_series, _max_drawdown


class TestMonthlySeries:
    def test_buckets_by_month_and_sorts(self):
        pairs = [
            ("2026-04-10", 0.05), ("2026-04-20", -0.02),
            ("2026-05-03", 0.03), ("2026-06-01", -0.06), ("2026-06-15", 0.10),
        ]
        s = _monthly_series(pairs)
        assert [m["ym"] for m in s] == ["2026-04", "2026-05", "2026-06"]
        apr = s[0]
        assert apr["n"] == 2 and apr["win_rate"] == 50.0
        assert apr["net"] == round((0.05 - 0.02) / 2 * 100, 2)
        assert s[2]["win_rate"] == 50.0 and s[2]["n"] == 2

    def test_empty_months_not_filled(self):
        # 4月和6月有, 5月空 → 只出两项, 不补5月
        pairs = [("2026-04-10", 0.05), ("2026-06-01", 0.03)]
        s = _monthly_series(pairs)
        assert [m["ym"] for m in s] == ["2026-04", "2026-06"]

    def test_empty_input(self):
        assert _monthly_series([]) == []


class TestMaxDrawdown:
    def test_peak_to_trough(self):
        # 等权累计: 10→20→12→25→30 ; peak=20后跌到12 → 回撤8; 后创新高不改最大回撤
        pairs = [("2026-01-01", 0.10), ("2026-01-02", 0.10),
                 ("2026-01-03", -0.08), ("2026-01-04", 0.13), ("2026-01-05", 0.05)]
        assert _max_drawdown(pairs) == 8.0

    def test_sorts_by_date_first(self):
        # 乱序输入应先按日期排序再算(同上序列打乱)
        pairs = [("2026-01-04", 0.13), ("2026-01-01", 0.10), ("2026-01-05", 0.05),
                 ("2026-01-03", -0.08), ("2026-01-02", 0.10)]
        assert _max_drawdown(pairs) == 8.0

    def test_monotonic_up_zero_drawdown(self):
        pairs = [("2026-01-0%d" % i, 0.02) for i in range(1, 7)]
        assert _max_drawdown(pairs) == 0.0

    def test_too_few_samples_returns_none(self):
        pairs = [("2026-01-01", -0.05)] * 4   # <5笔
        assert _max_drawdown(pairs) is None

    def test_all_losing_drawdown(self):
        pairs = [("2026-01-0%d" % i, -0.03) for i in range(1, 7)]
        # equity 单调下行 -3..-18, peak=0(起点前), 最大回撤=18
        assert _max_drawdown(pairs) == 18.0
