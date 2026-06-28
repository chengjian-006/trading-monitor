from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from backend.core.auth import verify_password, create_token, get_current_user
from backend.core.config import load_config
from backend.core.websocket import ws_manager
from backend.models import repository

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    user = await repository.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    cfg = load_config()
    sso_enabled = cfg.get("sso_enabled", True)

    if sso_enabled:
        tv = await repository.increment_token_version(user["id"])
        await ws_manager.kick_user(user["id"])
    else:
        tv = await repository.get_token_version(user["id"])

    token = create_token(user["id"], user["username"], user["role"], tv)

    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host if request.client else "unknown"
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
