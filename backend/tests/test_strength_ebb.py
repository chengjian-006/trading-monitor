"""强势退潮(打板赚钱效应转亏)阈值判定 + DB去重(重启安全)测试."""
from datetime import datetime
from unittest.mock import AsyncMock

from backend.services import market_ebb_detector as med
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


class TestStrengthEbbRestartSafe:
    """0703排查修复: 溢价维度原本只有内存哨兵, 部署重启后大盘风控卡当日重推。"""

    def _patch(self, monkeypatch, *, already_sent: bool):
        med._strength_alerted_date = None   # 模拟重启: 内存哨兵丢失

        class _FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 3, 13, 30)   # 固定盘中下午, 过11:00门

        monkeypatch.setattr(med, "datetime", _FakeDT)
        monkeypatch.setattr(med, "is_trading_time", lambda: True)
        monkeypatch.setattr(med.repository, "get_latest_emotion", AsyncMock(
            return_value={"trade_date": "2026-07-03", "yest_limit_up_premium": -0.77,
                          "name": "上证指数"}))
        monkeypatch.setattr(med.repository, "signal_already_sent_today",
                            AsyncMock(return_value=already_sent))
        save = AsyncMock()
        monkeypatch.setattr(med.repository, "save_signal", save)
        emit = AsyncMock()
        monkeypatch.setattr("backend.services.market_risk_controller.emit_risk_dimension", emit)
        return save, emit

    async def test_restart_with_db_record_no_repush(self, monkeypatch):
        # 重启后内存哨兵丢, 但DB已有当日 PLUNGE_STRENGTH_EBB → 不重推不重写
        save, emit = self._patch(monkeypatch, already_sent=True)
        await med.detect_strength_ebb()
        assert save.await_count == 0 and emit.await_count == 0
        # 内存哨兵已同步, 后续轮次直接短路
        assert med._strength_alerted_date == "2026-07-03"

    async def test_first_trigger_saves_then_emits(self, monkeypatch):
        save, emit = self._patch(monkeypatch, already_sent=False)
        await med.detect_strength_ebb()
        assert save.await_count == 1
        assert save.await_args[1]["signal_id"] == "PLUNGE_STRENGTH_EBB"
        assert emit.await_count == 1
