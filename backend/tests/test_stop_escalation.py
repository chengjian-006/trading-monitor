"""止损强制升级 纯逻辑测试 (v1.7.x).

只测可纯函数化的部分: 连续未执行天数、累计多亏、熄火(价格站回)判定、升级红卡文案。
DB 取信号/持仓、定时编排是集成层, 不在此测。
"""
from backend.services import stop_escalation as se


class TestConsecutiveStopDays:
    # trading_days_desc: 交易日, 最新在前
    DAYS = ["2026-06-19", "2026-06-18", "2026-06-17", "2026-06-16", "2026-06-15"]

    def test_fired_today_and_yesterday_is_two(self):
        run = se.consecutive_stop_days({"2026-06-19", "2026-06-18"}, self.DAYS)
        assert run == 2

    def test_fired_yesterday_and_before_but_not_today_still_counts(self):
        # 今日扫描早于止损触发/或今日盘中价在止损位上方未触发 → 今日跳过, 昨+前仍算连续
        run = se.consecutive_stop_days({"2026-06-18", "2026-06-17"}, self.DAYS)
        assert run == 2

    def test_fired_today_only_is_one(self):
        run = se.consecutive_stop_days({"2026-06-19"}, self.DAYS)
        assert run == 1

    def test_gap_breaks_run(self):
        # 今日触发, 昨日未触发(价格曾站回), 前日触发 → 只算今日这段(=1), 旧段不续
        run = se.consecutive_stop_days({"2026-06-19", "2026-06-17"}, self.DAYS)
        assert run == 1

    def test_no_fire_is_zero(self):
        assert se.consecutive_stop_days(set(), self.DAYS) == 0

    def test_stale_run_not_touching_recent_is_zero(self):
        # 连续段是陈旧历史(最近2交易日都没触发)→ 不算, 防陈旧误升级(沪电股份案例)
        # DAYS 最新=06-19; 触发只在 06-16/06-15(离今日隔了 06-19/06-18/06-17)
        run = se.consecutive_stop_days({"2026-06-16", "2026-06-15"}, self.DAYS)
        assert run == 0


class TestShouldEscalate:
    def test_two_days_escalates_at_n2(self):
        assert se.should_escalate(2, n=2) is True

    def test_one_day_does_not(self):
        assert se.should_escalate(1, n=2) is False

    def test_three_days_at_n3(self):
        assert se.should_escalate(2, n=3) is False
        assert se.should_escalate(3, n=3) is True


class TestExtraLoss:
    def test_holding_below_first_stop_is_positive_loss(self):
        # 首次止损 147.03 没砍, 现价 126.79, 持 200 股 → 多亏 (147.03-126.79)*200 = 4048
        assert se.extra_loss(147.03, 126.79, 200) == 4048

    def test_price_above_first_stop_is_negative(self):
        # 现价高于首次止损位(已回升) → 非正(不该再报, 熄火接管)
        assert se.extra_loss(147.03, 150.0, 200) < 0


class TestPriceRecovered:
    def test_above_first_stop_recovered(self):
        assert se.price_recovered(150.0, 147.03) is True

    def test_below_first_stop_not_recovered(self):
        assert se.price_recovered(126.79, 147.03) is False

    def test_equal_not_recovered(self):
        assert se.price_recovered(147.03, 147.03) is False


class TestBuildEscalationCard:
    def _card(self):
        return se.build_escalation_card(
            name="阳光电源", code="300274", day_n=3,
            first_stop_date="2026-06-12", first_stop_price=147.03, first_stop_pct=-12.0,
            current_price=126.79, current_pct=-24.3, extra_loss_yuan=4048,
            actions_md="🔕 当日不提醒　·　🔕 本周不提醒")

    def test_title_marks_days(self):
        title, _ = self._card()
        assert "止损未执行" in title and "3" in title and "阳光电源" in title

    def test_body_has_key_numbers(self):
        _, body = self._card()
        assert "300274" in body
        assert "147.03" in body            # 首次止损价
        assert "126.79" in body            # 现价
        assert "4,048" in body or "4048" in body   # 累计多亏(可带千分位)

    def test_body_carries_snooze_actions(self):
        _, body = self._card()
        assert "当日不提醒" in body and "本周不提醒" in body
