"""信号执行记录 — 用户主动标记某条信号 已执行/已跳过 + 填实际价/量/备注.

闭环价值:
  cfzy_biz_signals 记录"系统触发了什么", signal_outcome_backfill 算"严格跟单的话理论收益",
  这张表记录"用户实际怎么做的"。三者结合, 可以分析:
    - 用户的执行率 (signals 总数 vs executions executed 数)
    - 真实成交价 vs 信号触发价的偏移分布
    - 用户跳过的信号事后表现如何 (避坑率)
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Body, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/signal-executions", tags=["signal-executions"])


class ExecutionPayload(BaseModel):
    signal_pk: int = Field(..., description="cfzy_biz_signals.id")
    code: str = Field(..., max_length=10)
    action: str = Field(..., description="executed | skipped")
    actual_price: Optional[float] = None
    actual_qty: Optional[int] = None
    notes: Optional[str] = None


@router.post("")
async def upsert_execution(
    user: Annotated[dict, Depends(get_current_user)],
    payload: ExecutionPayload = Body(...),
):
    if payload.action not in ("executed", "skipped"):
        raise HTTPException(400, "action 必须是 executed 或 skipped")
    if payload.action == "executed" and payload.actual_price is not None and payload.actual_price <= 0:
        raise HTTPException(400, "actual_price 必须为正数 (留空表示按信号触发价)")
    rid = await repository.upsert_signal_execution(
        user_id=user["id"],
        signal_pk=payload.signal_pk,
        code=payload.code,
        action=payload.action,
        actual_price=payload.actual_price,
        actual_qty=payload.actual_qty,
        notes=(payload.notes or "").strip() or None,
    )
    return {"id": rid, "ok": True}


@router.delete("/{signal_pk}")
async def remove_execution(
    user: Annotated[dict, Depends(get_current_user)],
    signal_pk: int,
):
    await repository.delete_signal_execution(user["id"], signal_pk)
    return {"ok": True}


@router.get("")
async def list_executions(
    user: Annotated[dict, Depends(get_current_user)],
    signal_pks: Optional[str] = Query(None, description="逗号分隔的 signal_pk 列表, 不传则全部"),
):
    pks_list: Optional[list[int]] = None
    if signal_pks is not None:
        try:
            pks_list = [int(x) for x in signal_pks.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "signal_pks 必须是逗号分隔的整数")
    return await repository.list_signal_executions(user["id"], pks_list)
