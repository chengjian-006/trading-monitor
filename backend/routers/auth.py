import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.core.auth import verify_password, create_token, get_current_user
from backend.core.config import load_config
from backend.core.websocket import ws_manager
from backend.models import repository

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── 登录失败锁定 (应用层速率限制, 进程内存实现·单 worker 足够) ──
# 按 IP+用户名 计数: 10分钟窗口内失败 ≥5 次 → 锁 15 分钟(429), 成功登录清零该键。
LOGIN_FAIL_WINDOW = 10 * 60          # 失败计数窗口(秒)
LOGIN_FAIL_MAX = 5                   # 窗口内失败上限
LOGIN_LOCK_SECONDS = 15 * 60         # 锁定时长(秒)
_login_failures: dict[str, list[float]] = {}   # key → 窗口内失败时间戳
_login_locks: dict[str, float] = {}            # key → 锁定截止时间戳
_CLEANUP_INTERVAL = 60               # 过期清理最小间隔(秒), 防字典无限膨胀
_last_cleanup = 0.0


def _client_ip(request: Request) -> str:
    """取真实客户端 IP(用于登录失败锁定/日志)。

    优先 X-Real-IP(=nginx 的 $remote_addr, 客户端不可伪造); 退而取 X-Forwarded-For
    最后一段(我方 nginx proxy_add_x_forwarded_for 追加的真实IP在最右, 客户端伪造段只在左侧)。
    绝不取 XFF 首段——那是客户端完全可控的, 撞库者每次换首段即可绕过按IP的失败锁定。"""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _cleanup_login_guard(now: float) -> None:
    """惰性清理过期条目(每次登录最多触发一次, 间隔 ≥60s), 防内存无限膨胀。"""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    for key in [k for k, until in _login_locks.items() if until <= now]:
        _login_locks.pop(key, None)
    cutoff = now - LOGIN_FAIL_WINDOW
    for key in [k for k, ts in _login_failures.items() if not ts or ts[-1] <= cutoff]:
        _login_failures.pop(key, None)


def _check_login_lock(key: str, now: float) -> None:
    """锁定期内直接 429(带 Retry-After), 不碰 DB 不验密码。"""
    locked_until = _login_locks.get(key, 0.0)
    if locked_until > now:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请15分钟后再试",
            headers={"Retry-After": str(max(int(locked_until - now), 1))},
        )


def _record_login_failure(key: str, now: float) -> None:
    """记一次失败; 窗口内累计达上限 → 上锁并清空计数。"""
    fails = [t for t in _login_failures.get(key, []) if t > now - LOGIN_FAIL_WINDOW]
    fails.append(now)
    if len(fails) >= LOGIN_FAIL_MAX:
        _login_locks[key] = now + LOGIN_LOCK_SECONDS
        _login_failures.pop(key, None)
    else:
        _login_failures[key] = fails


class LoginRequest(BaseModel):
    username: str
    password: str = Field(max_length=256)


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    client_ip = _client_ip(request)
    guard_key = f"{client_ip}|{req.username}"
    now = time.time()
    _cleanup_login_guard(now)
    _check_login_lock(guard_key, now)     # 锁定判断在验证密码之前

    user = await repository.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"], user["salt"]):
        _record_login_failure(guard_key, now)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    # 登录成功: 清零该 IP+用户名 的失败计数与锁
    _login_failures.pop(guard_key, None)
    _login_locks.pop(guard_key, None)

    cfg = load_config()
    sso_enabled = cfg.get("sso_enabled", True)

    if sso_enabled:
        tv = await repository.increment_token_version(user["id"])
        await ws_manager.kick_user(user["id"])
    else:
        tv = await repository.get_token_version(user["id"])

    token = create_token(user["id"], user["username"], user["role"], tv)

    user_agent = request.headers.get("user-agent", "unknown")
    login_detail = {
        "ip": client_ip,
        "user_agent": user_agent,
        "sso_enabled": sso_enabled,
    }
    await repository.add_log(user["id"], user["username"], "login", user["username"], new_value=login_detail)

    return {
        "token": token,
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    }


@router.get("/me")
async def get_me(user: Annotated[dict, Depends(get_current_user)]):
    return user
