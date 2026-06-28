"""强势退潮(打板赚钱效应转亏)阈值判定测试."""
from backend.services.market_ebb_detector import _premium_ebb, PREMIUM_EBB_THRESHOLD


class TestPremiumEbb:
    def test_none(self):
        assert _premium_ebb(None, -0.5) is False

    def test_above_threshold(self):
        assert _premium_ebb(1.89, -0.5) is False
        assert _premium_ebb(0.0, -0.5) is False

    def test_at_or_below_threshold(self):
        assert _premium_ebb(-0.5, -0.5) is True
        assert _premium_ebb(-0.77, -0.5) is True

    def test_default_threshold_is_negative(self):
        assert PREMIUM_EBB_THRESHOLD < 0
