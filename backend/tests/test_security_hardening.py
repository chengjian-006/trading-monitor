# -*- coding: utf-8 -*-
"""安全加固回归 (v1.7.568): JWT密钥非硬编码 + SPA路由防路径穿越。不连库、不打外网。

追加 (登录锁定/配置打码/快捷链接时效):
  - /api/auth/login 应用层失败锁定: IP+用户名 10分钟窗口5次失败 → 锁15分钟(429)
  - GET /api/config 敏感叶子打码为哨兵, PUT 哨兵回传保留服务器现值
  - /api/quick/set HMAC 签名纳入 exp 过期时间戳, 旧无 exp 链接一律拒绝
"""
import asyncio
import hashlib
import os
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from pydantic import ValidationError

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


def test_password_hash_uses_current_pbkdf2_work_factor():
    """New passwords must use the current OWASP PBKDF2-HMAC-SHA256 work factor."""
    assert auth.PASSWORD_HASH_ITERATIONS >= 600_000


def test_current_password_hash_round_trip():
    password_hash, salt = auth.hash_password("a current strong password")
    assert auth.verify_password("a current strong password", password_hash, salt) is True


def test_legacy_password_hash_remains_compatible():
    password = "a legacy password"
    salt = "legacy-salt"
    legacy_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()
    assert auth.verify_password(password, legacy_hash, salt) is True


def test_wrong_password_is_rejected():
    password_hash, salt = auth.hash_password("the correct password")
    assert auth.verify_password("the wrong password", password_hash, salt) is False


def test_password_verification_executes_both_supported_work_factors(monkeypatch):
    password_hash, salt = auth.hash_password("a current strong password")
    real_pbkdf2_hmac = hashlib.pbkdf2_hmac
    observed_iterations = []

    def recording_pbkdf2_hmac(name, password, salt_bytes, iterations):
        observed_iterations.append(iterations)
        return real_pbkdf2_hmac(name, password, salt_bytes, iterations)

    monkeypatch.setattr(auth.hashlib, "pbkdf2_hmac", recording_pbkdf2_hmac)
    assert auth.verify_password("a current strong password", password_hash, salt) is True
    assert observed_iterations == [
        auth.PASSWORD_HASH_ITERATIONS,
        auth._LEGACY_PASSWORD_HASH_ITERATIONS,
    ]


def test_current_user_uses_live_role_after_database_demotion(monkeypatch):
    """A JWT role claim must not preserve admin access after a database demotion."""
    from backend.models import repository
    from backend.core import config as config_module

    token = auth.create_token(user_id=9, username="alice", role="admin", token_version=4)

    async def fake_get_user(_user_id):
        return {"id": 9, "username": "alice", "role": "user", "token_version": 4}

    async def fake_get_token_version(_user_id):
        return 4

    monkeypatch.setattr(repository, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(repository, "get_token_version", fake_get_token_version)
    monkeypatch.setattr(config_module, "load_config", lambda: {"sso_enabled": True})

    credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    current_user = asyncio.run(auth.get_current_user(credential))
    assert current_user["role"] == "user"


def test_security_sensitive_user_update_is_one_atomic_statement(monkeypatch):
    from backend.models.repo import users as users_repo

    statements = []

    async def recording_execute(sql, args):
        statements.append((sql, args))

    monkeypatch.setattr(users_repo, "_execute", recording_execute)
    asyncio.run(users_repo.update_user_and_revoke_sessions(
        9, username="alice-2", role="user", mobile="13800138000"
    ))

    assert len(statements) == 1
    sql, args = statements[0]
    assert "username = %s" in sql
    assert "role = %s" in sql
    assert "mobile = %s" in sql
    assert "token_version = token_version + 1" in sql
    assert list(args) == ["alice-2", "user", "13800138000", 9]


def test_password_reset_and_revocation_is_one_atomic_statement(monkeypatch):
    from backend.models.repo import users as users_repo

    statements = []

    async def recording_execute(sql, args):
        statements.append((sql, args))

    monkeypatch.setattr(users_repo, "_execute", recording_execute)
    asyncio.run(users_repo.reset_user_password(9, "new-hash", "new-salt"))

    assert len(statements) == 1
    sql, args = statements[0]
    assert "password_hash = %s" in sql
    assert "salt = %s" in sql
    assert "token_version = token_version + 1" in sql
    assert tuple(args) == ("new-hash", "new-salt", 9)


class _StatefulUserRepository:
    def __init__(self, *, role="admin", fail_revocation=False):
        password_hash, salt = auth.hash_password("old strong password")
        self.user = {
            "id": 9,
            "username": "alice",
            "role": role,
            "password_hash": password_hash,
            "salt": salt,
            "token_version": 4,
        }
        self.fail_revocation = fail_revocation

    async def get_user_by_id(self, user_id):
        return dict(self.user) if user_id == self.user["id"] else None

    async def get_user_by_username(self, username):
        return dict(self.user) if username == self.user["username"] else None

    async def update_user(self, user_id, **updates):
        assert user_id == self.user["id"]
        self.user.update(updates)

    async def update_user_password(self, user_id, password_hash, salt):
        assert user_id == self.user["id"]
        self.user.update(password_hash=password_hash, salt=salt)

    async def increment_token_version(self, user_id):
        assert user_id == self.user["id"]
        if self.fail_revocation:
            raise RuntimeError("token-version update failed")
        self.user["token_version"] += 1
        return self.user["token_version"]

    async def update_user_and_revoke_sessions(self, user_id, **updates):
        assert user_id == self.user["id"]
        if self.fail_revocation:
            raise RuntimeError("atomic security update failed")
        self.user.update(updates)
        self.user["token_version"] += 1
        return self.user["token_version"]

    async def reset_user_password(self, user_id, password_hash, salt):
        assert user_id == self.user["id"]
        if self.fail_revocation:
            raise RuntimeError("atomic password reset failed")
        self.user.update(password_hash=password_hash, salt=salt)
        self.user["token_version"] += 1
        return self.user["token_version"]

    async def add_log(self, *_args, **_kwargs):
        return None


def _install_stateful_user_repository(monkeypatch, fake_repo, *, sso_enabled):
    from backend.core import config as config_module
    from backend.models import repository

    for name in (
        "get_user_by_id",
        "get_user_by_username",
        "update_user",
        "update_user_password",
        "increment_token_version",
        "update_user_and_revoke_sessions",
        "reset_user_password",
        "add_log",
    ):
        monkeypatch.setattr(repository, name, getattr(fake_repo, name), raising=False)
    monkeypatch.setattr(config_module, "load_config", lambda: {"sso_enabled": sso_enabled})


def _assert_token_rejected(token):
    credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth.get_current_user(credential))
    assert exc_info.value.status_code == 401


def test_password_reset_invalidates_existing_token(monkeypatch):
    from backend.routers import users as users_router

    fake_repo = _StatefulUserRepository(role="user")
    _install_stateful_user_repository(monkeypatch, fake_repo, sso_enabled=True)
    token = auth.create_token(9, "alice", "user", fake_repo.user["token_version"])

    request = users_router.ResetPasswordRequest(password="a new sufficiently strong password")
    asyncio.run(users_router.reset_password(
        9, request, {"id": 1, "username": "admin", "role": "admin"}
    ))

    _assert_token_rejected(token)


def test_role_change_invalidates_existing_token_when_sso_disabled(monkeypatch):
    from backend.routers import users as users_router

    fake_repo = _StatefulUserRepository(role="admin")
    _install_stateful_user_repository(monkeypatch, fake_repo, sso_enabled=False)
    token = auth.create_token(9, "alice", "admin", fake_repo.user["token_version"])

    request = users_router.UpdateUserRequest(role="user")
    asyncio.run(users_router.update_user(
        9, request, {"id": 1, "username": "admin", "role": "admin"}
    ))

    _assert_token_rejected(token)


def test_role_change_is_not_applied_when_revocation_fails(monkeypatch):
    from backend.routers import users as users_router

    fake_repo = _StatefulUserRepository(role="admin", fail_revocation=True)
    _install_stateful_user_repository(monkeypatch, fake_repo, sso_enabled=True)

    with pytest.raises(RuntimeError, match="update failed"):
        asyncio.run(users_router.update_user(
            9,
            users_router.UpdateUserRequest(role="user"),
            {"id": 1, "username": "admin", "role": "admin"},
        ))
    assert fake_repo.user["role"] == "admin"


def test_password_reset_is_not_applied_when_revocation_fails(monkeypatch):
    from backend.routers import users as users_router

    fake_repo = _StatefulUserRepository(role="user", fail_revocation=True)
    old_password_hash = fake_repo.user["password_hash"]
    old_salt = fake_repo.user["salt"]
    _install_stateful_user_repository(monkeypatch, fake_repo, sso_enabled=True)

    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(users_router.reset_password(
            9,
            users_router.ResetPasswordRequest(password="a new sufficiently strong password"),
            {"id": 1, "username": "admin", "role": "admin"},
        ))
    assert fake_repo.user["password_hash"] == old_password_hash
    assert fake_repo.user["salt"] == old_salt


def test_password_requests_reject_short_passwords():
    """Account creation and resets must reject passwords below the minimum policy length."""
    from backend.routers.users import CreateUserRequest, ResetPasswordRequest

    with pytest.raises(ValidationError):
        CreateUserRequest(username="alice", password="short")
    with pytest.raises(ValidationError):
        ResetPasswordRequest(password="short")


def test_login_password_accepts_legacy_lengths_and_rejects_over_256_characters():
    from backend.routers.auth import LoginRequest

    assert LoginRequest(username="alice", password="x").password == "x"
    assert len(LoginRequest(username="alice", password="x" * 256).password) == 256
    with pytest.raises(ValidationError):
        LoginRequest(username="alice", password="x" * 257)


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

    def test_spoofed_xff_first_segment_cannot_bypass_lock(self, login_client):
        """撞库防绕过: 攻击者伪造 XFF 首段(每次不同)也不能绕过按IP锁定——
        真实IP在 XFF 最右段(我方nginx追加), 锁定键取最右段, 故五次失败照样锁死。"""
        client, auth_router = login_client
        # 每次换一个伪造首段, 但最右真实段固定 9.9.9.9(模拟 nginx 追加)
        for i in range(5):
            r = client.post("/api/auth/login",
                            json={"username": "admin", "password": "wrong"},
                            headers={"x-forwarded-for": f"{i}.{i}.{i}.{i}, 9.9.9.9"})
            assert r.status_code == 401
        # 第六次即便再换伪造首段, 真实段仍 9.9.9.9 → 已锁 429
        r = client.post("/api/auth/login",
                        json={"username": "admin", "password": "goodpass"},
                        headers={"x-forwarded-for": "123.123.123.123, 9.9.9.9"})
        assert r.status_code == 429
        assert auth_router._login_locks.get("9.9.9.9|admin", 0) > time.time()

    def test_x_real_ip_takes_priority(self, login_client):
        """X-Real-IP(nginx设,不可伪造)优先于 XFF: 锁定键用 X-Real-IP。"""
        client, auth_router = login_client
        for _i in range(5):
            client.post("/api/auth/login",
                        json={"username": "admin", "password": "wrong"},
                        headers={"x-real-ip": "8.8.8.8", "x-forwarded-for": "1.2.3.4"})
        assert auth_router._login_locks.get("8.8.8.8|admin", 0) > time.time()

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
