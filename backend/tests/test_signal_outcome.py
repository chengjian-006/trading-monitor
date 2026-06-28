"""信号 outcome 判定阈值测试 — _judge_outcome 的边界行为."""
from backend.services.signal_outcome_backfill import (
    _judge_outcome,
    SUCCESS_THRESHOLD,
    FAIL_THRESHOLD,
)


class TestJudgeOutcome:
    def test_none_input_returns_none(self):
        assert _judge_outcome(None) is None

    def test_at_success_threshold_inclusive(self):
        # p5 == +5.0 (默认) 算 success
        assert _judge_outcome(SUCCESS_THRESHOLD) == "success"

    def test_above_success_threshold(self):
        assert _judge_outcome(SUCCESS_THRESHOLD + 0.1) == "success"
        assert _judge_outcome(12.5) == "success"

    def test_at_fail_threshold_inclusive(self):
        # p5 == -3.0 算 fail
        assert _judge_outcome(FAIL_THRESHOLD) == "fail"

    def test_below_fail_threshold(self):
        assert _judge_outcome(FAIL_THRESHOLD - 0.1) == "fail"
        assert _judge_outcome(-10) == "fail"

    def test_neutral_between(self):
        # 介于 -3 ~ +5 之间是 neutral
        assert _judge_outcome(0) == "neutral"
        assert _judge_outcome(2.5) == "neutral"
        assert _judge_outcome(-2.9) == "neutral"
        assert _judge_outcome(SUCCESS_THRESHOLD - 0.01) == "neutral"
        assert _judge_outcome(FAIL_THRESHOLD + 0.01) == "neutral"
