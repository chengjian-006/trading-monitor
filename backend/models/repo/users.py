"""用户 CRUD + token version + ths_path + profile - cfzy_sys_users 表."""
import aiomysql

from backend.models.database import get_pool
from backend.models.repo._db import _execute, _fetchall, _fetchone


async def get_user_by_username(username: str) -> dict | None:
    return await _fetchone("SELECT * FROM cfzy_sys_users WHERE username = %s", (username,))


async def get_user_by_id(user_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM cfzy_sys_users WHERE id = %s", (user_id,))


async def create_user(username: str, password_hash: str, salt: str, role: str = "user") -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO cfzy_sys_users (username, password_hash, salt, role) VALUES (%s, %s, %s, %s)",
                (username, password_hash, salt, role),
            )
            return cur.lastrowid


async def list_users() -> list[dict]:
    return await _fetchall(
        "SELECT id, username, role, wecom_webhook, push_enabled, lark_webhook, lark_enabled, created_at "
        "FROM cfzy_sys_users ORDER BY id"
    )


async def update_user(user_id: int, **kwargs):
    allowed = {"username", "role", "wecom_webhook", "push_enabled", "mobile", "lark_webhook", "lark_enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [user_id]
    await _execute(f"UPDATE cfzy_sys_users SET {set_clause} WHERE id = %s", values)


async def update_user_and_revoke_sessions(user_id: int, **kwargs):
    """Apply account identity/authorization changes and revoke tokens atomically."""
    allowed = {"username", "role", "wecom_webhook", "push_enabled", "mobile", "lark_webhook", "lark_enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [user_id]
    await _execute(
        f"UPDATE cfzy_sys_users SET {set_clause}, token_version = token_version + 1 WHERE id = %s",
        values,
    )


async def delete_user(user_id: int):
    await _execute("DELETE FROM cfzy_biz_stock_pool WHERE user_id = %s", (user_id,))
    await _execute("DELETE FROM cfzy_biz_signals WHERE user_id = %s", (user_id,))
    await _execute("DELETE FROM cfzy_sys_users WHERE id = %s", (user_id,))


async def get_token_version(user_id: int) -> int:
    row = await _fetchone("SELECT token_version FROM cfzy_sys_users WHERE id = %s", (user_id,))
    return row["token_version"] if row else 0


async def increment_token_version(user_id: int) -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "UPDATE cfzy_sys_users SET token_version = token_version + 1 WHERE id = %s", (user_id,)
            )
            await cur.execute("SELECT token_version FROM cfzy_sys_users WHERE id = %s", (user_id,))
            row = await cur.fetchone()
            return row["token_version"]


async def update_user_password(user_id: int, password_hash: str, salt: str):
    await _execute(
        "UPDATE cfzy_sys_users SET password_hash = %s, salt = %s WHERE id = %s",
        (password_hash, salt, user_id),
    )


async def reset_user_password(user_id: int, password_hash: str, salt: str):
    """Replace password material and revoke existing tokens in one statement."""
    await _execute(
        "UPDATE cfzy_sys_users SET password_hash = %s, salt = %s, "
        "token_version = token_version + 1 WHERE id = %s",
        (password_hash, salt, user_id),
    )


async def get_user_ths_path(user_id: int) -> str:
    row = await _fetchone("SELECT ths_path FROM cfzy_sys_users WHERE id = %s", (user_id,))
    return row["ths_path"] if row else ""


async def update_user_ths_path(user_id: int, ths_path: str):
    await _execute("UPDATE cfzy_sys_users SET ths_path = %s WHERE id = %s", (ths_path, user_id))


async def update_user_profile(user_id: int, **kwargs):
    allowed = {"wecom_webhook", "push_enabled", "lark_webhook", "lark_enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [user_id]
    await _execute(f"UPDATE cfzy_sys_users SET {set_clause} WHERE id = %s", values)
