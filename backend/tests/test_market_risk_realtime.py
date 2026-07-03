"""盘中实时风险检测 — 降级/撤销防刷屏回归测试 (0703实况修复).

背景: EOD基线连续多日RED时, 盘中行情回暖 → 旧逻辑"解除预警→写回EOD基线(RED)→
下轮又判定需解除"每5分钟循环, 一天推了8张错标"降至YELLOW"的卡(实际写回的是RED)。
修复: 回退目标状态不低于当前状态 = 无可解除, 不写不推; 基线去留由16:40收盘复核定夺。
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import market_risk_controller as mrc


def _rows(n_up: int, n_down: int, up_pct: float = 2.0, down_pct: float = -2.0):
    return ([{"code": f"6{i:05d}", "pct_change": up_pct} for i in range(n_up)] +
            [{"code": f"0{i:05d}", "pct_change": down_pct} for i in range(n_down)])


def _patch_env(monkeypatch, *, rows, existing, prev_eod):
    """把实时检测的外部依赖全部替换掉, 只留纯逻辑."""
    monkeypatch.setattr("backend.core.trading_calendar.is_trading_time", lambda: True)
    monkeypatch.setattr(mrc, "_get_row", AsyncMock(return_value=existing))
    monkeypatch.setattr(mrc, "_get_prev_state", AsyncMock(return_value=prev_eod))
    upsert = AsyncMock()
    monkeypatch.setattr(mrc, "_upsert_risk", upsert)
    card = AsyncMock()
    monkeypatch.setattr(mrc, "_push_state_card", card)
    fetchall = AsyncMock(return_value=rows)
    monkeypatch.setattr("backend.models.repo._db._fetchall", fetchall)
    mrc._realtime_push_count.clear()
    return upsert, card


def _fake_time(monkeypatch, hhmm: str = "13:05"):
    import backend.services.market_risk_controller as m
    from datetime import datetime as real_dt

    class _FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 7, 3, int(hhmm[:2]), int(hhmm[3:]))

    monkeypatch.setattr(m, "datetime", _FakeDT)


class TestDowngradeNoLoop:
    async def test_recovered_but_eod_baseline_red_stays_silent(self, monkeypatch):
        # 行情已全面转好(should_be=GREEN), 今日实时状态RED, 但EOD基线也是RED
        # → 无可解除: 不推卡、不写库(0703刷屏场景)
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card = _patch_env(monkeypatch, rows=_rows(60, 30),
                                  existing=existing, prev_eod=mrc.RED)
        await mrc.market_risk_realtime()
        assert card.await_count == 0
        assert upsert.await_count == 0

    async def test_recovered_with_green_baseline_pushes_once(self, monkeypatch):
        # EOD基线GREEN → 正常解除: 推一张GREEN卡并写回GREEN
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card = _patch_env(monkeypatch, rows=_rows(60, 30),
                                  existing=existing, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert "解除" in card.await_args[0][0]
        assert upsert.await_args[0][1]["state"] == mrc.GREEN

    async def test_red_downgrades_to_yellow_once(self, monkeypatch):
        # 部分回暖(should_be=YELLOW), 当前RED → 降一级YELLOW, 推一张降级卡
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        # 涨跌比 40%(>28) 但均收益 -1.5%(< -1) → YELLOW
        rows = _rows(40, 60, up_pct=1.0, down_pct=-3.2)
        upsert, card = _patch_env(monkeypatch, rows=rows,
                                  existing=existing, prev_eod=mrc.RED)
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert "降为谨慎" in card.await_args[0][0]
        assert upsert.await_args[0][1]["state"] == mrc.YELLOW

    async def test_after_downgrade_next_round_silent(self, monkeypatch):
        # 上一轮已降到YELLOW落库, 行情继续回暖到GREEN但基线YELLOW=prev_eod
        # → fallback(YELLOW) 与当前(YELLOW)持平, 不再重复推
        _fake_time(monkeypatch)
        existing = {"state": mrc.YELLOW, "source": "realtime"}
        upsert, card = _patch_env(monkeypatch, rows=_rows(60, 30),
                                  existing=existing, prev_eod=mrc.YELLOW)
        await mrc.market_risk_realtime()
        assert card.await_count == 0
        assert upsert.await_count == 0


class TestUpgradeStillWorks:
    async def test_green_to_red_upgrade_pushes(self, monkeypatch):
        _fake_time(monkeypatch)
        # 涨跌比 10%(<22) 且均收益 -3.5%(<-2) → RED
        rows = _rows(10, 90, up_pct=0.5, down_pct=-4.0)
        upsert, card = _patch_env(monkeypatch, rows=rows, existing=None, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert "空仓" in card.await_args[0][0]
        assert upsert.await_args[0][1]["state"] == mrc.RED

    async def test_daily_push_cap(self, monkeypatch):
        _fake_time(monkeypatch)
        rows = _rows(10, 90, up_pct=0.5, down_pct=-4.0)
        upsert, card = _patch_env(monkeypatch, rows=rows, existing=None, prev_eod=mrc.GREEN)
        mrc._realtime_push_count["2026-07-03"] = mrc._REALTIME_MAX_PUSHES
        await mrc.market_risk_realtime()
        assert card.await_count == 0


class TestStateCardBuild:
    async def test_card_is_compact_plain_text(self, monkeypatch):
        # 极简卡: 状态迁移行 + 白话行 + 👉建议, 走 send_dual 纯文本卡, 无表格
        from unittest.mock import AsyncMock
        from backend.services import notifier
        sent = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_dual", sent)
        await mrc._push_state_card("🟡 盘面回暖·降为谨慎", "yellow", mrc.RED, mrc.YELLOW,
                                   ["盘面回暖: 63%在涨"], "先别重仓.")
        assert sent.await_count == 1
        body = sent.await_args[0][0]
        assert "🔴 空仓  →  🟡 谨慎" in body
        assert "👉 先别重仓." in body
        assert sent.await_args[1]["template"] == "yellow"
