"""博主 cookie 续签接口单测 (v1.7.x).

直接调 async 端点函数 + monkeypatch, 不起 FastAPI app(避开 lifespan 连库)、不打网。
覆盖: token 守门 / 空凭证拒绝 / 新凭证无效则不落库(写坏拒绝) / 有效则落库并启用。
"""
import asyncio
from datetime import datetime

from backend.routers import blogger as bl
from backend.fetcher.ths_blogger import BloggerFetchError


def _setup(monkeypatch, token="SECRET", fetch=None):
    """注入: 原始config(含renew_token) + 捕获 save_config + 可选 fetch 桩。返回 saved 容器。"""
    monkeypatch.setattr(bl, "_read_raw_config",
                        lambda: {"blogger_tracking": {"renew_token": token, "blogger_name": "全能的野人"},
                                 "database": {"host": "keep"}})  # database 等其它键应原样保留
    saved = {}
    monkeypatch.setattr(bl.cfgmod, "save_config", lambda cfg: saved.update(cfg=cfg))
    if fetch is not None:
        monkeypatch.setattr(bl, "fetch_posts_with_creds", fetch)
    return saved


def test_bad_token_rejected(monkeypatch):
    _setup(monkeypatch)
    res = asyncio.run(bl.renew_cookie(bl.RenewRequest(token="WRONG", cookie="c", hexin_v="h")))
    assert res["ok"] is False and "token" in res["error"]


def test_empty_cookie_rejected(monkeypatch):
    _setup(monkeypatch)
    res = asyncio.run(bl.renew_cookie(bl.RenewRequest(token="SECRET", cookie="", hexin_v="")))
    assert res["ok"] is False


def test_invalid_creds_not_saved(monkeypatch):
    async def boom(*a, **k):
        raise BloggerFetchError("status_code=2 (Cookie/签名可能过期)")
    saved = _setup(monkeypatch, fetch=boom)
    res = asyncio.run(bl.renew_cookie(bl.RenewRequest(token="SECRET", cookie="c", hexin_v="h")))
    assert res["ok"] is False
    assert "cfg" not in saved   # 写坏拒绝: 校验不过绝不落库


def test_valid_creds_saved_and_enabled(monkeypatch):
    async def ok(*a, **k):
        return [{"posted_at": datetime(2026, 6, 16, 15, 31), "content": "x"}]
    saved = _setup(monkeypatch, fetch=ok)
    res = asyncio.run(bl.renew_cookie(
        bl.RenewRequest(token="SECRET", cookie="newcookie", hexin_v="newhexin")))
    assert res["ok"] is True and res["posts_count"] == 1
    assert res["latest_post_time"] == "2026-06-16 15:31"
    bt = saved["cfg"]["blogger_tracking"]
    assert bt["cookie"] == "newcookie" and bt["hexin_v"] == "newhexin"
    assert bt["enabled"] is True and "renewed_at" in bt
    assert saved["cfg"]["database"] == {"host": "keep"}   # 其它配置键原样保留, 没被覆盖
