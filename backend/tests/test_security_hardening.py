# -*- coding: utf-8 -*-
"""安全加固回归 (v1.7.568): JWT密钥非硬编码 + SPA路由防路径穿越。不连库、不打外网。

追加 (登录锁定/配置打码/快捷链接时效):
  - /api/auth/login 应用层失败锁定: IP+用户名 10分钟窗口5次失败 → 锁15分钟(429)
  - GET /api/config 敏感叶子打码为哨兵, PUT 哨兵回传保留服务器现值
  - /api/quick/set HMAC 签名纳入 exp 过期时间戳, 旧无 exp 链接一律拒绝
"""
import os
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import auth


def test_secret_key_is_not_the_old_hardcoded_value():
    """旧硬编码密钥必须已从源码消失(否则伪造token风险仍在)。"""
    assert auth.SECRET_KEY != "guxiaocha-jwt-secret-2026-trading-monitor"
    assert len(auth.SECRET_KEY) >= 32


def test_token_roundtrip_with_resolved_key():
    """用运行期密钥签发的 token 能被解回, 字段完整。"""
    tok = auth.create_token(user_id=1, username="u", role="admin", token_version=3)
    payload = auth.decode_token(tok)
    assert payload["sub"] == 1
    assert payload["role"] == "admin"
    assert payload["tv"] == 3


def test_spa_path_traversal_blocked():
    """SPA 兜底路由: dist 外的路径(穿越)不返回真实文件, 回落 index.html。"""
    from backend import main
    dist = os.path.normpath(main.FRONTEND_DIST)
    # 模拟穿越目标: dist 上跳两级的 config.json
    evil = os.path.normpath(os.path.join(dist, "../../config.json"))
    in_dist = evil == dist or evil.startswith(dist + os.sep)
    assert in_dist is False   # 穿越路径不在 dist 内 → 逻辑会回落 index.html
    # 正常资源仍判定在 dist 内
    good = os.path.normpath(os.path.join(dist, "assets/app.js"))
    assert good.startswith(dist + os.sep)


# ══════════════════════════════════════════════════════════════
# 问题1: /api/auth/login 应用层失败锁定
# ══════════════════════════════════════════════════════════════

@pytest.fixture()
def login_client(monkeypatch):
    """只挂 auth 路由的极简 app; DB/配置/日志全打桩, 不连库。密码 goodpass=对。"""
    from backend.routers import auth as auth_router

    auth_router._login_failures.clear()
    auth_router._login_locks.clear()

    fake_user = {"id": 1, "username": "admin", "role": "admin",
                 "password_hash": "h", "salt": "s"}

    async def fake_get_user(username):
        return fake_user if username == "admin" else None

    async def fake_get_tv(user_id):
        return 1

    async def fake_add_log(*a, **kw):
        return None

    monkeypatch.setattr(auth_router.repository, "get_user_by_username", fake_get_user)
    monkeypatch.setattr(auth_router.repository, "get_token_version", fake_get_tv)
    monkeypatch.setattr(auth_router.repository, "add_log", fake_add_log)
    monkeypatch.setattr(auth_router, "verify_password",
                        lambda pw, h, s: pw == "goodpass")
    monkeypatch.setattr(auth_router, "load_config", lambda: {"sso_enabled": False})

    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)
    yield client, auth_router


def _login(client, password, ip=None, username="admin"):
    headers = {"x-forwarded-for": ip} if ip else {}
    return client.post("/api/auth/login",
                       json={"username": username, "password": password},
                       headers=headers)


class TestLoginLockout:
    def test_sixth_attempt_locked_with_429(self, login_client):
        client, _ = login_client
        for _i in range(5):
            assert _login(client, "wrong").status_code == 401
        resp = _login(client, "wrong")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0
        assert "尝试次数过多" in resp.json()["detail"]

    def test_locked_even_with_correct_password(self, login_client):
        client, _ = login_client
        for _i in range(5):
            _login(client, "wrong")
        # 锁定期内即使密码正确也 429 (锁定判断在验证密码之前)
        assert _login(client, "goodpass").status_code == 429

    def test_success_resets_counter(self, login_client):
        client, _ = login_client
        for _i in range(4):
            assert _login(client, "wrong").status_code == 401
        assert _login(client, "goodpass").status_code == 200   # 成功清零
        # 清零后再错 4 次仍是 401 (未累计到 5), 且随后能正常登录
        for _i in range(4):
            assert _login(client, "wrong").status_code == 401
        assert _login(client, "goodpass").status_code == 200

    def test_different_ip_not_affected(self, login_client):
        client, _ = login_client
        for _i in range(5):
            _login(client, "wrong", ip="1.1.1.1")
        assert _login(client, "wrong", ip="1.1.1.1").status_code == 429   # 该 IP 已锁
        assert _login(client, "goodpass", ip="2.2.2.2").status_code == 200  # 别的 IP 不受影响

    def test_lock_expires_after_lock_window(self, login_client):
        client, auth_router = login_client
        for _i in range(5):
            _login(client, "wrong", ip="3.3.3.3")
        key = "3.3.3.3|admin"
        assert auth_router._login_locks.get(key, 0) > time.time()
        # 模拟 15 分钟已过: 锁到期 → 解锁, 可正常登录
        auth_router._login_locks[key] = time.time() - 1
        assert _login(client, "goodpass", ip="3.3.3.3").status_code == 200

    def test_failures_outside_window_do_not_lock(self, login_client):
        client, auth_router = login_client
        key = "4.4.4.4|admin"
        # 窗口(10分钟)外的 4 次旧失败不计数: 再错 1 次只是 401 不触发锁
        stale = time.time() - auth_router.LOGIN_FAIL_WINDOW - 60
        auth_router._login_failures[key] = [stale] * 4
        assert _login(client, "wrong", ip="4.4.4.4").status_code == 401
        assert key not in auth_router._login_locks


# ══════════════════════════════════════════════════════════════
# 问题2: GET /api/config 敏感字段打码 + PUT 哨兵不覆盖
# ══════════════════════════════════════════════════════════════

_SAMPLE_CFG = {
    "jwt_secret": "real-jwt-secret-abc",
    "pushplus_token": "pp-token-123",
    "wxpusher_token": "wx-token-456",
    "anthropic_api_key": "sk-ant-xyz",
    "lark_webhook": "https://open.feishu.cn/hook/abc",
    "lark_enabled": True,
    "scan_interval_seconds": 6,
    "hotkey": "ctrl+k",                       # 词边界: 不该被误伤
    "database": {"host": "1.2.3.4", "port": 3306, "user": "u",
                 "password": "dbpass-999", "db": "d"},
    "blogger_tracking": {
        "enabled": False,
        "request": {"headers": {"Cookie": "session=verysecret"}},
    },
}

_PLAINTEXT_SECRETS = ["real-jwt-secret-abc", "pp-token-123", "wx-token-456",
                      "sk-ant-xyz", "dbpass-999", "session=verysecret"]


@pytest.fixture()
def config_client(monkeypatch):
    """只挂 config 路由的极简 app; load/save/日志打桩, require_admin 直接放行。"""
    import copy
    from backend.core.auth import require_admin
    from backend.routers import config as config_router

    store = {"cfg": copy.deepcopy(_SAMPLE_CFG), "saved": None}

    def fake_load():
        return copy.deepcopy(store["cfg"])

    def fake_save(cfg):
        store["saved"] = copy.deepcopy(cfg)
        store["cfg"] = copy.deepcopy(cfg)

    async def fake_add_log(*a, **kw):
        return None

    monkeypatch.setattr(config_router, "load_config", fake_load)
    monkeypatch.setattr(config_router, "save_config", fake_save)
    monkeypatch.setattr(config_router.repository, "add_log", fake_add_log)

    app = FastAPI()
    app.include_router(config_router.router)
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "username": "a", "role": "admin"}
    yield TestClient(app), store, config_router


class TestConfigMasking:
    def test_mask_helper_masks_sensitive_leaves_recursively(self):
        from backend.routers import config as config_router
        masked = config_router.mask_secrets(_SAMPLE_CFG)
        assert masked["jwt_secret"] == config_router.MASK_SENTINEL
        assert masked["pushplus_token"] == config_router.MASK_SENTINEL
        assert masked["anthropic_api_key"] == config_router.MASK_SENTINEL
        assert masked["database"]["password"] == config_router.MASK_SENTINEL
        assert masked["blogger_tracking"]["request"]["headers"]["Cookie"] == config_router.MASK_SENTINEL
        # 非敏感字段原样保留
        assert masked["hotkey"] == "ctrl+k"
        assert masked["scan_interval_seconds"] == 6
        assert masked["database"]["host"] == "1.2.3.4"
        assert masked["lark_enabled"] is True

    def test_get_config_returns_no_plaintext_secret(self, config_client):
        client, _store, _mod = config_client
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.text
        for secret in _PLAINTEXT_SECRETS:
            assert secret not in body

    def test_put_sentinel_preserves_server_value(self, config_client):
        client, store, mod = config_client
        resp = client.post("/api/config", json={"pushplus_token": mod.MASK_SENTINEL,
                                                "scan_interval_seconds": 9})
        assert resp.status_code == 200
        assert store["saved"]["pushplus_token"] == "pp-token-123"   # 哨兵→保留现值
        assert store["saved"]["scan_interval_seconds"] == 9         # 普通字段正常写入

    def test_put_real_new_value_writes_through(self, config_client):
        client, store, _mod = config_client
        client.post("/api/config", json={"pushplus_token": "NEW-TOKEN"})
        assert store["saved"]["pushplus_token"] == "NEW-TOKEN"

    def test_get_then_put_back_keeps_every_secret(self, config_client):
        """前端流: GET(打码) → 原样 PUT 回去, 任何敏感字段都不能被哨兵破坏。"""
        client, store, _mod = config_client
        masked = client.get("/api/config").json()
        resp = client.post("/api/config", json=masked)
        assert resp.status_code == 200
        saved = store["saved"]
        assert saved["jwt_secret"] == "real-jwt-secret-abc"
        assert saved["pushplus_token"] == "pp-token-123"
        assert saved["wxpusher_token"] == "wx-token-456"
        assert saved["anthropic_api_key"] == "sk-ant-xyz"
        assert saved["database"]["password"] == "dbpass-999"
        assert saved["blogger_tracking"]["request"]["headers"]["Cookie"] == "session=verysecret"
        # 非敏感字段也原样
        assert saved["hotkey"] == "ctrl+k"
        assert saved["database"]["host"] == "1.2.3.4"


# ══════════════════════════════════════════════════════════════
# 问题3: /api/quick/set 快捷链接 HMAC 纳入 exp 时效
# ══════════════════════════════════════════════════════════════

@pytest.fixture()
def quick_client(monkeypatch):
    """只挂 quick 路由的极简 app; 落库打桩, 记录 add_pref 是否被调用。"""
    from backend.routers import quick as quick_router

    calls = []

    async def fake_add_pref(user_id, kind, target, until):
        calls.append((user_id, kind, target, until))

    monkeypatch.setattr(quick_router.pref_repo, "add_pref", fake_add_pref)

    app = FastAPI()
    app.include_router(quick_router.router)
    yield TestClient(app), calls


def _link_params(url: str) -> dict:
    q = parse_qs(urlsplit(url).query)
    return {k: v[0] for k, v in q.items()}


class TestQuickLinkExpiry:
    # 2026-07: kind=snooze/mute 已随「静音此股/今日免打扰」拆除, 时效链路测试改用仍在役的 stop_snooze
    def test_built_link_carries_exp(self):
        from backend.services import push_pref as pp
        url = pp.build_quick_link("http://x.cn", 1, "stop_snooze", target="300166", days=3)
        params = _link_params(url)
        assert "exp" in params and "sig" in params
        # 默认时效 48 小时
        assert abs(int(params["exp"]) - (time.time() + pp.QUICK_LINK_TTL_SECONDS)) < 10

    def test_valid_link_accepted(self, quick_client):
        from backend.services import push_pref as pp
        client, calls = quick_client
        url = pp.build_quick_link("http://x.cn", 1, "stop_snooze", target="300166", days=3)
        resp = client.get("/api/quick/set", params=_link_params(url))
        assert resp.status_code == 200
        assert "已设置" in resp.text
        assert len(calls) == 1 and calls[0][1] == "stop_snooze" and calls[0][2] == "300166"

    def test_removed_kind_link_rejected(self, quick_client):
        # 旧卡片里已拆除功能(如 snooze 按票全压)的真签名链接 → 无效操作, 不落库
        from backend.services import push_pref as pp
        client, calls = quick_client
        url = pp.build_quick_link("http://x.cn", 1, "snooze", target="300166", days=3)
        resp = client.get("/api/quick/set", params=_link_params(url))
        assert resp.status_code == 200
        assert "无效操作" in resp.text
        assert calls == []

    def test_expired_link_rejected(self, quick_client):
        from backend.services import push_pref as pp
        client, calls = quick_client
        exp = int(time.time()) - 10   # 已过期, 但签名是真的
        sig = pp.sign_params(1, "stop_snooze", "300166", 3, exp)
        resp = client.get("/api/quick/set",
                          params={"u": 1, "k": "stop_snooze", "t": "300166", "d": 3,
                                  "exp": exp, "sig": sig})
        assert resp.status_code == 200   # 友好页面而非报错
        assert "链接已过期" in resp.text and "最新推送卡片" in resp.text
        assert calls == []

    def test_legacy_link_without_exp_rejected(self, quick_client):
        from backend.services import push_pref as pp
        client, calls = quick_client
        legacy_sig = pp.sign("1|stop_snooze|300166|3")   # 旧版签名原文(无 exp)
        resp = client.get("/api/quick/set",
                          params={"u": 1, "k": "stop_snooze", "t": "300166", "d": 3,
                                  "sig": legacy_sig})
        assert "已失效" in resp.text or "已过期" in resp.text
        assert calls == []

    def test_tampered_exp_rejected(self, quick_client):
        from backend.services import push_pref as pp
        client, calls = quick_client
        exp = int(time.time()) + 100
        sig = pp.sign_params(1, "stop_snooze", "300166", 3, exp)
        resp = client.get("/api/quick/set",
                          params={"u": 1, "k": "stop_snooze", "t": "300166", "d": 3,
                                  "exp": exp + 99999, "sig": sig})   # 篡改 exp 续命
        assert "已失效" in resp.text
        assert calls == []

    def test_verify_params_requires_exp(self):
        from backend.services import push_pref as pp
        exp = int(time.time()) + 100
        sig = pp.sign_params(1, "ack", "", 0, exp)
        assert pp.verify_params(1, "ack", "", 0, exp, sig) is True
        assert pp.verify_params(1, "ack", "", 0, None, sig) is False
        assert pp.verify_params(1, "ack", "", 0, exp + 1, sig) is False
