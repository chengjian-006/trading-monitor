import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


def _resolve_secret_key() -> str:
    """JWT 签名密钥来源 (v1.7.568): 优先读 config.json 的 jwt_secret;
    缺失则生成随机密钥并回写 config.json 持久化 (单用户系统首次启动自愈, 之后稳定不变)。

    Why: 原来密钥硬编码在源码里且随仓库进了 GitHub, 任何看到这行的人都能伪造 admin token
    接管公网面板。改为运行期密钥后, 源码不再含任何可用密钥。首次生效时现有登录态失效需重登一次。
    """
    from backend.core.config import load_config, save_config
    try:
        cfg = load_config()
    except Exception:
        return secrets.token_urlsafe(48)   # 读不到配置: 本进程用随机密钥兜底(重启需重登, 仍比硬编码安全)
    key = (cfg.get("jwt_secret") or "").strip()
    if key:
        return key
    key = secrets.token_urlsafe(48)
    try:
        cfg["jwt_secret"] = key
        save_config(cfg)
        logger.warning("[auth] 未配置 jwt_secret, 已生成随机密钥并写入 config.json (现有登录态需重新登录一次)")
    except Exception as e:
        logger.warning(f"[auth] jwt_secret 回写 config.json 失败, 本进程用临时密钥(重启需重登): {e}")
    return key


SECRET_KEY = _resolve_secret_key()
ALGORITHM = "HS256"
TOKEN_EXPIRE_SECONDS = 7 * 24 * 3600
PASSWORD_HASH_ITERATIONS = 600_000
_LEGACY_PASSWORD_HASH_ITERATIONS = 100_000

_bearer = HTTPBearer()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(32).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_HASH_ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    password_bytes = password.encode()
    salt_bytes = salt.encode()
    for iterations in (PASSWORD_HASH_ITERATIONS, _LEGACY_PASSWORD_HASH_ITERATIONS):
        digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt_bytes, iterations).hex()
        if hmac.compare_digest(digest, password_hash):
            return True
    return False


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
    user = await repository.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效，请重新登录")

    cfg = load_config()
    if cfg.get("sso_enabled", True):
        db_tv = user.get("token_version")
        if db_tv != payload.get("tv"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效，请重新登录")
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


async def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user
