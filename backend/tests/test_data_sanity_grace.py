# -*- coding: utf-8 -*-
"""行情自检"恢复宽限窗"判定 (v1.7.562) — 开盘/午休回来前几分钟不判陈旧。"""
from datetime import datetime
from unittest.mock import patch

from backend.services.data_sanity import _in_resume_grace


def _t(h, m, s=0):
    return datetime(2026, 7, 3, h, m, s)


def test_lunch_resume_in_grace():
    # 13:00 恢复后前 3 分钟内 → 宽限
    assert _in_resume_grace(_t(13, 0, 5)) is True
    assert _in_resume_grace(_t(13, 2, 59)) is True


def test_lunch_resume_after_grace():
    assert _in_resume_grace(_t(13, 3, 0)) is False
    assert _in_resume_grace(_t(13, 10)) is False


def test_open_resume_in_grace():
    # 开盘(含集合竞价撮合)后前 3 分钟 → 宽限; 开盘时刻取自 config trading_hours(v1.7.594 起 09:15)。
    # patch 固定 config 使断言不依赖部署环境的 config.json。
    with patch("backend.core.config.load_config",
               return_value={"trading_hours": [{"start": "09:15", "end": "11:30"},
                                               {"start": "13:00", "end": "15:00"}]}):
        assert _in_resume_grace(_t(9, 15, 30)) is True     # 开盘后 30s, 宽限内
        assert _in_resume_grace(_t(9, 18, 30)) is False    # 开盘后 3.5min, 已过 3min 宽限
        assert _in_resume_grace(_t(9, 25, 30)) is False    # 开盘后 10min, 早已过宽限


def test_normal_intraday_not_grace():
    assert _in_resume_grace(_t(10, 30)) is False
    assert _in_resume_grace(_t(14, 0)) is False


def test_before_resume_not_grace():
    # 时段开始之前(午休中)不算宽限 — 本就不在交易时段, 自检另有闸门
    assert _in_resume_grace(_t(12, 59)) is False


# ── v1.7.x: "行情缺失"(null_price)早盘连续竞价前不误报 ──
# 0715 09:18 报「165/165 只无价」= 开盘挪到09:15后, 集合竞价撮合(09:25)前全池无成交价的结构性误报。
from unittest.mock import AsyncMock, patch  # noqa: E402
import backend.services.data_sanity as ds  # noqa: E402

_TH = {"trading_hours": [{"start": "09:15", "end": "11:30"},
                         {"start": "13:00", "end": "15:00"}]}


def test_before_am_continuous_precontinuous():
    # 开盘(09:15)~连续竞价(09:30)之间 → True(撮合前全池无价属正常, 缺失告警应跳过)
    with patch("backend.core.config.load_config", return_value=_TH):
        assert ds._before_am_continuous(_t(9, 15, 30)) is True
        assert ds._before_am_continuous(_t(9, 18, 0)) is True     # 0715 误报时刻
        assert ds._before_am_continuous(_t(9, 29, 59)) is True


def test_before_am_continuous_after_open():
    with patch("backend.core.config.load_config", return_value=_TH):
        assert ds._before_am_continuous(_t(9, 30, 0)) is False    # 连续竞价起, 真缺失照报
        assert ds._before_am_continuous(_t(9, 35, 0)) is False
        assert ds._before_am_continuous(_t(13, 0, 30)) is False   # 下午复盘已有价, 不豁免
        assert ds._before_am_continuous(_t(14, 0, 0)) is False


async def _run_sanity(null_price, before_am, stale=0):
    ds._last_alert_at = 0.0
    sent = []
    with patch.object(ds, "is_trading_time", return_value=True), \
         patch.object(ds, "_before_am_continuous", return_value=before_am), \
         patch.object(ds, "_in_resume_grace", return_value=False), \
         patch("backend.services.data_health.flush_data_health", new=AsyncMock()), \
         patch("backend.models.repository.count_quote_health",
               new=AsyncMock(return_value={"total": 165, "stale": stale,
                                           "null_price": null_price})), \
         patch("backend.services.notifier.send_wechat_text",
               new=AsyncMock(side_effect=lambda t: sent.append(t))):
        await ds.check_data_sanity()
    return sent


async def test_null_price_suppressed_precontinuous():
    # 09:18 竞价撮合前全池无价 → 不告警(误报被压)
    sent = await _run_sanity(null_price=165, before_am=True)
    assert sent == []


async def test_null_price_alerts_after_continuous():
    # 连续竞价(09:30)后仍全池无价 → 真缺失, 照常告警
    sent = await _run_sanity(null_price=165, before_am=False)
    assert len(sent) == 1 and "行情缺失" in sent[0]
