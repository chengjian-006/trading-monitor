"""rally_reminder 卖出提醒持仓闸门单测 (v1.7.525).

_should_notify: 卖出提醒(止盈减半/止损/时间止损)只对真实持仓推送。
  - 真实持仓集里有该 code → 推 (True)
  - 不在持仓里 → 不推 (False), 但落库+喂统计照常(由 handler 无条件执行, 非本函数职责)
  - held 为 None(查持仓失败/无法判定) → 兜底放行推送 (True), 不漏真实持仓风控提醒
纯函数, 不连库不联网。
"""
from backend.services.rally_reminder import _should_notify


def test_held_code_notifies():
    assert _should_notify("603986", {"603986", "000725"}) is True


def test_unheld_code_suppressed():
    # 虚拟模型跟踪(从没真买)→ 不推
    assert _should_notify("603986", {"000725"}) is False


def test_empty_holdings_suppresses_all():
    # 空仓 → 任何卖出提醒都不推
    assert _should_notify("603986", set()) is False


def test_none_holdings_fails_open():
    # 查持仓失败(None)→ 兜底放行, 宁可多推也不漏真实持仓的止损提醒
    assert _should_notify("603986", None) is True
