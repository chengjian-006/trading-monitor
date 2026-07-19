"""图鉴回撤+逐月胜率 纯函数测试 (功能10, v1.7.x).

_monthly_series: 近6月交易 [(date,ret)] → 逐月 [{ym,win_rate,n,net}] 升序, 空月不补。
_max_drawdown: 按触发日分批(当日等权)的复利权益曲线, 峰到谷最大回撤(百分点正数, 0~100); 样本<5→None。
  v1.7.716 前是"逐笔累加百分点", 会随样本数线性膨胀(实测弱势极限 2328笔 → -3134%), 已改。
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
        # 复利: 1.10→1.21→1.1132→…; peak=1.21 跌到 1.1132 → (1.21-1.1132)/1.21=8.0%; 后创新高不改
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
        # 复利单调下行: 1-0.97^6 = 16.7%
        assert _max_drawdown(pairs) == 16.7

    # ── v1.7.716 口径修复的三条守门断言 ──

    def test_never_exceeds_100_percent(self):
        """回撤不可能超过 -100%。旧口径下这条必挂(-3134%), 是这次修复的核心。"""
        pairs = [("2026-%02d-%02d" % (m, d), -0.05)
                 for m in range(1, 7) for d in range(1, 29)]      # 168 个连亏日
        dd = _max_drawdown(pairs)
        assert dd is not None and 0 <= dd <= 100, f"回撤越界: {dd}"

    def test_same_day_signals_share_one_batch(self):
        """同一天触发的多个信号是"资金等分"而非"连续满仓下注"。"""
        one = [("2026-01-01", 0.10), ("2026-01-02", -0.10), ("2026-01-03", 0.02),
               ("2026-01-04", 0.02), ("2026-01-05", 0.02)]
        # 把 01-02 那笔 -10% 拆成同日 4 笔(均值仍 -10%) → 回撤应不变
        split = [p for p in one if p[0] != "2026-01-02"] + [("2026-01-02", -0.10)] * 4
        assert _max_drawdown(split) == _max_drawdown(one)

    def test_drawdown_does_not_scale_with_sample_count(self):
        """高频模型不该仅因为触发得多就显得更危险 —— 旧口径正是这么坏掉的。

        两个模型同样的日收益节奏, 一个每天 1 笔、一个每天 20 笔, 回撤必须一致。
        """
        days = [("2026-01-%02d" % d, (0.03 if d % 3 else -0.05)) for d in range(1, 25)]
        dense = [p for p in days for _ in range(20)]
        assert _max_drawdown(dense) == _max_drawdown(days)
