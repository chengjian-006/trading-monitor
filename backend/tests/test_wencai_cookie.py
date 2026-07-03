"""问财带登录 cookie 请求 — v1.7.573 降低风控概率的回归测试."""
import sys
import types

import pytest

from backend.fetcher import wencai_screener as ws


class _FakeDF:
    """伪装成非空 DataFrame(_normalize_rows 只需 empty/columns/head/iterrows)。"""
    empty = False
    columns = ["code", "股票简称[20260703]", "最新价[20260703]", "最新涨跌幅[20260703]"]

    def head(self, n):
        return self

    def iterrows(self):
        yield 0, {"code": "301379", "股票简称[20260703]": "天山电子",
                  "最新价[20260703]": 32.16, "最新涨跌幅[20260703]": 20.0}


def _install_fake_pywencai(monkeypatch, captured):
    fake = types.ModuleType("pywencai")

    def _get(**kwargs):
        captured.update(kwargs)
        return _FakeDF()

    fake.get = _get
    monkeypatch.setitem(sys.modules, "pywencai", fake)
    # pandas 需真实(用于 _normalize_rows isinstance 判断) — _FakeDF 不是真 DataFrame,
    # 故直接 stub _normalize_rows 只验证调用参数
    monkeypatch.setattr(ws, "_normalize_rows", lambda df, limit: [{"code": "301379"}])


class TestCookiePassed:
    async def test_cookie_from_config_is_passed(self, monkeypatch):
        captured: dict = {}
        _install_fake_pywencai(monkeypatch, captured)
        monkeypatch.setattr(ws, "_login_cookie", lambda: "utk=abc; user=xx")
        await ws.fetch_wencai("今日涨停", limit=10)
        assert captured.get("cookie") == "utk=abc; user=xx"
        assert captured.get("question") == "今日涨停"

    async def test_no_cookie_falls_back_anonymous(self, monkeypatch):
        captured: dict = {}
        _install_fake_pywencai(monkeypatch, captured)
        monkeypatch.setattr(ws, "_login_cookie", lambda: "")
        await ws.fetch_wencai("今日涨停", limit=10)
        assert "cookie" not in captured   # 空 cookie 走匿名路径, 不传 cookie 参数

    def test_login_cookie_reads_config(self, monkeypatch):
        import backend.core.config as cfg
        monkeypatch.setattr(cfg, "load_config",
                            lambda: {"blogger_tracking": {"cookie": "abc123"}})
        assert ws._login_cookie() == "abc123"

    def test_login_cookie_safe_when_missing(self, monkeypatch):
        import backend.core.config as cfg
        monkeypatch.setattr(cfg, "load_config", lambda: {})
        assert ws._login_cookie() == ""
