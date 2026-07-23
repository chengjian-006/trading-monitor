from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.core.auth import get_current_user, hash_password, require_admin
from backend.models import repository

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "user"] = "user"


class UpdateProfileRequest(BaseModel):
    lark_webhook: Optional[str] = None
    lark_enabled: Optional[int] = None


class UpdateUserRequest(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=64)
    role: Optional[Literal["admin", "user"]] = None
    mobile: Optional[str] = None
    lark_webhook: Optional[str] = None
    lark_enabled: Optional[int] = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=256)


@router.get("")
async def list_users(_: Annotated[dict, Depends(require_admin)]):
    return await repository.list_users()


@router.post("")
async def create_user(req: CreateUserRequest, admin: Annotated[dict, Depends(require_admin)]):
    existing = await repository.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")
    pw_hash, salt = hash_password(req.password)
    user_id = await repository.create_user(req.username, pw_hash, salt, req.role)
    await repository.add_log(admin["id"], admin["username"], "create_user", req.username,
                             new_value={"username": req.username, "role": req.role})
    return {"ok": True, "id": user_id, "username": req.username}


@router.put("/{user_id:int}")  # :int 防止与字面子路径(如 /profile)冲突, 否则 PUT /profile 会被当 user_id 解析报 422
async def update_user(user_id: int, req: UpdateUserRequest, admin: Annotated[dict, Depends(require_admin)]):
    user = await repository.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True}
    if "username" in updates and updates["username"] != user["username"]:
        existing = await repository.get_user_by_username(updates["username"])
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")
    if "role" in updates or "username" in updates:
        await repository.update_user_and_revoke_sessions(user_id, **updates)
    else:
        await repository.update_user(user_id, **updates)
    await repository.add_log(admin["id"], admin["username"], "update_user", user["username"],
                             new_value=updates)
    return {"ok": True}


@router.delete("/{user_id:int}")
async def delete_user(user_id: int, admin: Annotated[dict, Depends(require_admin)]):
    if user_id == admin["id"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己")
    user = await repository.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    await repository.delete_user(user_id)
    await repository.add_log(admin["id"], admin["username"], "delete_user", user["username"],
                             old_value={"username": user["username"], "role": user["role"]})
    return {"ok": True}


@router.post("/{user_id:int}/reset-password")
async def reset_password(user_id: int, req: ResetPasswordRequest, admin: Annotated[dict, Depends(require_admin)]):
    user = await repository.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    pw_hash, salt = hash_password(req.password)
    await repository.reset_user_password(user_id, pw_hash, salt)
    await repository.add_log(admin["id"], admin["username"], "reset_password", user["username"])
    return {"ok": True}


@router.get("/profile")
async def get_profile(user: Annotated[dict, Depends(get_current_user)]):
    full = await repository.get_user_by_id(user["id"])
    return {
        "lark_webhook": full.get("lark_webhook", ""),
        "lark_enabled": full.get("lark_enabled", 0),
    }


@router.put("/profile")
async def update_profile(req: UpdateProfileRequest, user: Annotated[dict, Depends(get_current_user)]):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await repository.update_user_profile(user["id"], **updates)
    return {"ok": True}


@router.post("/test-lark-push")
async def test_user_lark_push(user: Annotated[dict, Depends(get_current_user)]):
    full = await repository.get_user_by_id(user["id"])
    webhook = full.get("lark_webhook", "")
    if not webhook:
        return {"ok": False, "msg": "请先配置个人飞书Webhook地址"}
    from backend.services.lark_notifier import send_lark_test
    ok, msg = await send_lark_test(webhook)
    return {"ok": ok, "msg": msg}
