"""盘中实时风险检测 — 防抽风(缓冲带+冷静期)回归测试 (v1.7.588)。

背景1(0703): EOD基线连续RED时盘中回暖→旧逻辑循环推假"降级"卡。修复: 不解除到基线以下。
背景2(0707): 指标贴着单条阈值来回穿→10分钟内"空仓→降谨慎"自己打脸。修复:
  - 降级用缓冲带(退出线远高于进入线): 脱离RED需涨跌比≥35%且均值≥-1.2%;
  - 冷静期: 任何降级距上次变档≥30分钟;
  - 升级(转差)不受缓冲带/冷静期约束, 危险要及时报。
"""
from datetime import datetime as _dt
from unittest.mock import AsyncMock

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
    dismiss = AsyncMock()
    monkeypatch.setattr(mrc, "_push_dismiss", dismiss)
    fetchall = AsyncMock(return_value=rows)
    monkeypatch.setattr("backend.models.repo._db._fetchall", fetchall)
    mrc._realtime_push_count.clear()
    mrc._realtime_last_change_at.clear()
    return upsert, card, dismiss


def _fake_time(monkeypatch, hhmm: str = "13:05"):
    import backend.services.market_risk_controller as m

    class _FakeDT(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt(2026, 7, 3, int(hhmm[:2]), int(hhmm[3:]))

    monkeypatch.setattr(m, "datetime", _FakeDT)


class TestDowngradeBufferBand:
    async def test_marginal_recovery_within_buffer_stays_silent(self, monkeypatch):
        # 0707打脸场景: 涨跌比刚爬过进入线(30%>22)但没到退出线(<35), 均值仍深跌(-1.8)
        # → 维持RED, 不推不写(旧逻辑会误降YELLOW自打脸)。基线GREEN以隔离缓冲带效应。
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(30, 70, up_pct=1.0, down_pct=-3.0),
                                           existing=existing, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert card.await_count == 0 and dismiss.await_count == 0
        assert upsert.await_count == 0

    async def test_clear_recovery_past_buffer_downgrades_to_yellow(self, monkeypatch):
        # 明显转好过RED退出线(涨跌比40≥35 且 均值-0.8≥-1.2)但未到GREEN线 → 降到谨慎, 推一张
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(40, 60, up_pct=2.0, down_pct=-2.67),
                                           existing=existing, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert "谨慎" in card.await_args[0][0]
        assert upsert.await_args[0][1]["state"] == mrc.YELLOW

    async def test_full_recovery_past_green_line_clears(self, monkeypatch):
        # 全面回稳过GREEN退出线(涨跌比60≥45 且 均值0.8≥-0.3) → 解除卡(基线v1.1 灰header), 写回GREEN
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(60, 40, up_pct=2.0, down_pct=-1.0),
                                           existing=existing, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert dismiss.await_count == 1 and card.await_count == 0
        c = dismiss.await_args[0][0]
        assert "预警解除" in c.title
        assert c.template == "grey"                       # 解除卡=灰header中性收尾
        assert "今日解除" in c.subtitle                    # 副标题时间线
        assert "解除条件" in c.elements[0]["content"]      # 写明是哪个条件解除的
        assert upsert.await_args[0][1]["state"] == mrc.GREEN

    async def test_downgrade_blocked_by_eod_baseline(self, monkeypatch):
        # 行情全面转好但EOD基线RED → 不解除到基线以下(0703场景), 静默
        _fake_time(monkeypatch)
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(60, 40, up_pct=2.0, down_pct=-1.0),
                                           existing=existing, prev_eod=mrc.RED)
        await mrc.market_risk_realtime()
        assert card.await_count == 0 and dismiss.await_count == 0
        assert upsert.await_count == 0

    async def test_downgrade_blocked_by_cooldown(self, monkeypatch):
        # 明显转好可解除, 但距上次变档仅7分钟(<30) → 冷静期内先按兵不动, 不打脸
        _fake_time(monkeypatch, "13:05")
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(60, 40, up_pct=2.0, down_pct=-1.0),
                                           existing=existing, prev_eod=mrc.GREEN)
        mrc._realtime_last_change_at["2026-07-03"] = _dt(2026, 7, 3, 12, 58)
        await mrc.market_risk_realtime()
        assert card.await_count == 0 and dismiss.await_count == 0
        assert upsert.await_count == 0

    async def test_downgrade_allowed_after_cooldown(self, monkeypatch):
        # 同上但距上次变档已40分钟(≥30) → 正常解除(解除卡带发布时刻时间线)
        _fake_time(monkeypatch, "13:40")
        existing = {"state": mrc.RED, "source": "realtime"}
        upsert, card, dismiss = _patch_env(monkeypatch, rows=_rows(60, 40, up_pct=2.0, down_pct=-1.0),
                                           existing=existing, prev_eod=mrc.GREEN)
        mrc._realtime_last_change_at["2026-07-03"] = _dt(2026, 7, 3, 13, 0)
        await mrc.market_risk_realtime()
        assert dismiss.await_count == 1
        assert "今日 13:00" in dismiss.await_args[0][0].subtitle   # 发布时刻=上次变档时刻
        assert upsert.await_args[0][1]["state"] == mrc.GREEN


class TestUpgradeStillWorks:
    async def test_green_to_red_upgrade_pushes(self, monkeypatch):
        _fake_time(monkeypatch)
        # 涨跌比 10%(<22) 且均收益 -3.55%(<-2) → RED
        rows = _rows(10, 90, up_pct=0.5, down_pct=-4.0)
        upsert, card, dismiss = _patch_env(monkeypatch, rows=rows, existing=None, prev_eod=mrc.GREEN)
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert "空仓" in card.await_args[0][0]
        assert upsert.await_args[0][1]["state"] == mrc.RED

    async def test_upgrade_ignores_cooldown(self, monkeypatch):
        # 升级(转差)不受冷静期约束: 即使刚变档过, 危险仍及时报
        _fake_time(monkeypatch, "13:05")
        rows = _rows(10, 90, up_pct=0.5, down_pct=-4.0)
        upsert, card, dismiss = _patch_env(monkeypatch, rows=rows,
                                           existing={"state": mrc.YELLOW, "source": "realtime"},
                                           prev_eod=mrc.GREEN)
        mrc._realtime_last_change_at["2026-07-03"] = _dt(2026, 7, 3, 13, 2)  # 3分钟前刚变档
        await mrc.market_risk_realtime()
        assert card.await_count == 1
        assert upsert.await_args[0][1]["state"] == mrc.RED

    async def test_daily_push_cap(self, monkeypatch):
        _fake_time(monkeypatch)
        rows = _rows(10, 90, up_pct=0.5, down_pct=-4.0)
        upsert, card, dismiss = _patch_env(monkeypatch, rows=rows, existing=None, prev_eod=mrc.GREEN)
        mrc._realtime_push_count["2026-07-03"] = mrc._REALTIME_MAX_PUSHES
        await mrc.market_risk_realtime()
        assert card.await_count == 0 and dismiss.await_count == 0


class TestStateCardBuild:
    async def test_card_uses_dual_card_with_elements(self, monkeypatch):
        # 元素卡(基线v1.1): 状态迁移(带档位) + 白话 + [为什么] + 👉建议 + 信封字段(摘要/状态彩签)
        from backend.services import notifier
        sent = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_dual_card", sent)
        await mrc._push_state_card("🟡 市场风险 · 降到「谨慎」档", "orange", mrc.RED, mrc.YELLOW,
                                   ["跌势缓和：42%在涨"], "别重仓。", why="退出线：≥35%在涨",
                                   summary="空仓降到谨慎 42%在涨")
        assert sent.await_count == 1
        text = sent.await_args[0][0]
        assert "🔴 空仓" in text and "🟡 谨慎（谨慎档）" in text
        assert "👉 别重仓。" in text
        assert "退出线：≥35%在涨" in text
        kw = sent.await_args[1]
        assert kw["template"] == "orange"   # 基线五大家族: 风险谨慎档 = orange
        assert isinstance(kw["elements"], list) and len(kw["elements"]) == 4  # head+白话+why+建议
        assert kw["summary"] == "空仓降到谨慎 42%在涨"       # 锁屏摘要(基线v1.1标配)
        assert kw["text_tags"] == [("谨慎", "orange")]        # 状态名彩签

    async def test_card_summary_defaults_to_state_head(self, monkeypatch):
        from backend.services import notifier
        sent = AsyncMock(return_value=True)
        monkeypatch.setattr(notifier, "send_dual_card", sent)
        await mrc._push_state_card("🔴 空仓预警", "red", mrc.GREEN, mrc.RED, ["盘面大跌"], "先保命")
        kw = sent.await_args[1]
        assert "空仓" in kw["summary"]
        assert kw["text_tags"] == [("空仓", "red")]
