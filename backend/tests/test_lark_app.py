# -*- coding: utf-8 -*-
"""飞书应用机器人通道 (v1.7.631) 单测: 回调按钮构造 / 快捷动作执行器 / 发送路由。"""
import asyncio

from backend.services import lark_app, lark_notifier
from backend.services import push_pref as pref_svc


def _run(coro):
    return asyncio.run(coro)


class TestButtons:
    def test_callback_button_shape(self):
        b = lark_app.callback_button("✅ 已卖出", {"k": "mark_sold", "u": 1, "t": "002929", "d": 365},
                                     style="primary")
        assert b["tag"] == "button" and b["type"] == "primary"
        assert b["text"]["content"] == "✅ 已卖出"
        assert b["behaviors"][0]["type"] == "callback"
        assert b["behaviors"][0]["value"]["k"] == "mark_sold"

    def test_button_row_columns(self):
        row = lark_app.button_row([lark_app.callback_button("a", {"k": "ack", "u": 1, "t": "", "d": 0})])
        assert row["tag"] == "column_set" and len(row["columns"]) == 1
        assert row["columns"][0]["elements"][0]["tag"] == "button"

    def test_quick_action_value(self):
        v = lark_app.quick_action_value(1, "snooze_until_retrigger", "002929|BUY_X", 0)
        assert v == {"k": "snooze_until_retrigger", "u": 1, "t": "002929|BUY_X", "d": 0}

    def test_signal_button_rows_sell_has_mark_sold(self):
        # 2026-07 拆除「今日免打扰/静音今日/静音本周」后: 卖出卡=已卖出+静到再突破
        rows = pref_svc.build_quick_action_button_rows(1, "002929", "SELL_X", "sell")
        texts = [c["elements"][0]["text"]["content"] for r in rows for c in r["columns"]]
        assert texts == ["✅ 已卖出", "🔕 静到再突破"]

    def test_signal_button_rows_buy_only_retrigger(self):
        rows = pref_svc.build_quick_action_button_rows(1, "002929", "BUY_X", "buy")
        texts = [c["elements"][0]["text"]["content"] for r in rows for c in r["columns"]]
        assert texts == ["🔕 静到再突破"]

    def test_signal_button_rows_empty_without_stock(self):
        # 大盘预警(无 code/signal_id)不再有任何按钮行(原来还给「今日免打扰」)
        assert pref_svc.build_quick_action_button_rows(1, "", "", "plunge") == []

    def test_surge_button_rows(self):
        rows = pref_svc.build_surge_action_button_rows(1, "002929")
        texts = [c["elements"][0]["text"]["content"] for r in rows for c in r["columns"]]
        assert texts == ["🔕 当日不提醒", "🔕 本周不提醒"]


class TestExecuteQuickAction:
    def test_retrigger_snooze_and_mark_sold(self, monkeypatch):
        calls = {}

        async def fake_add_pref(u, k, t, until):
            calls["pref"] = (u, k, t)

        async def fake_update_stock(code, user_id, **kw):
            calls["stock"] = (code, user_id, kw)

        from backend.models.repo import push_pref as pref_repo
        from backend.models import repository
        monkeypatch.setattr(pref_repo, "add_pref", fake_add_pref)
        monkeypatch.setattr(repository, "update_stock", fake_update_stock)

        ok, label, detail = _run(pref_svc.execute_quick_action(
            1, "snooze_until_retrigger", "002929|BUY_X", 0))
        assert ok and "直到再突破" in label and "002929" in detail
        assert calls["pref"] == (1, "snooze_until_retrigger", "002929|BUY_X")

        ok, label, detail = _run(pref_svc.execute_quick_action(1, "mark_sold", "002929", 365))
        assert ok and "已卖出" in label
        assert calls["stock"][0] == "002929" and calls["stock"][2] == {"status": "watch"}

    def test_invalid_kind(self):
        ok, label, _ = _run(pref_svc.execute_quick_action(1, "nope", "", 0))
        assert not ok and label == "无效操作"

    def test_removed_kinds_rejected(self):
        # 2026-07 拆除「今日免打扰/静音此股」: 旧卡片里的 mute/unmute/snooze 链接点进来一律无效
        for k in ("mute", "unmute", "snooze"):
            ok, label, _ = _run(pref_svc.execute_quick_action(1, k, "", 0))
            assert not ok and label == "无效操作"


class TestPostRouting:
    def test_app_channel_first_then_no_webhook(self, monkeypatch):
        # 应用通道启用且发送成功 → 不再走 webhook
        monkeypatch.setattr(lark_app, "enabled", lambda: True)

        async def fake_send(payload):
            return True
        monkeypatch.setattr(lark_app, "send_card_payload", fake_send)

        called = {"webhook": False}

        class _Boom:
            def __init__(self, *a, **k):
                called["webhook"] = True
                raise AssertionError("不应走到 webhook")
        monkeypatch.setattr("httpx.AsyncClient", _Boom)

        ok = _run(lark_notifier._post("https://open.feishu.cn/hook/x",
                                      {"msg_type": "interactive", "card": {"a": 1}}, "t"))
        assert ok and not called["webhook"]

    def test_app_channel_failure_falls_back(self, monkeypatch):
        # 应用通道失败 → 回退 webhook(这里 webhook 为空直接 False, 验证不抛异常)
        monkeypatch.setattr(lark_app, "enabled", lambda: True)

        async def fake_send(payload):
            return False
        monkeypatch.setattr(lark_app, "send_card_payload", fake_send)
        ok = _run(lark_notifier._post("", {"msg_type": "interactive", "card": {}}, "t"))
        assert ok is False

    def test_disabled_keeps_webhook_only(self, monkeypatch):
        monkeypatch.setattr(lark_app, "enabled", lambda: False)
        ok = _run(lark_notifier._post("", {"msg_type": "interactive", "card": {}}, "t"))
        assert ok is False
