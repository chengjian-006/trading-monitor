import hashlib
import os
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET_KEY = "guxiaocha-jwt-secret-2026-trading-monitor"
ALGORITHM = "HS256"
TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600

_bearer = HTTPBearer()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(32).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return dk.hex() == password_hash


def create_token(user_id: int, username: str, role: str, token_version: int = 1) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "tv": token_version,
        "exp": int(time.time()) + TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        payload["sub"] = int(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效Token")


async def get_current_user(
    cred: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> dict:
    payload = decode_token(cred.credentials)
    from backend.models import repository
    from backend.core.config import load_config
    cfg = load_config()
    if cfg.get("sso_enabled", True):
        db_tv = await repository.get_token_version(payload["sub"])
        if payload.get("tv") and db_tv != payload["tv"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效，请重新登录")
    return {"id": payload["sub"], "username": payload["username"], "role": payload["role"]}


async def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
