"""交易日记 API - cfzy_biz_trade_journal (v1.7.669).

手动记录每笔买卖的理由/心态/复盘。当前登录用户维度; 与交割单交易分析(客观数据)互补。
"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.core.auth import get_current_user
from backend.models import repository

router = APIRouter(prefix="/api/trade-journal", tags=["trade-journal"])


class JournalBody(BaseModel):
    code: str = ""
    name: str = ""
    side: str = ""              # buy / sell / hold / note
    trade_date: Optional[str] = None
    price: Optional[float] = None
    qty: Optional[int] = None
    reason: str = ""
    emotion: str = ""
    review: str = ""


@router.get("")
async def list_journal(user: Annotated[dict, Depends(get_current_user)]):
    return await repository.list_journal(user["id"])


@router.post("")
async def create_journal(body: JournalBody, user: Annotated[dict, Depends(get_current_user)]):
    jid = await repository.create_journal(user["id"], body.model_dump())
    return {"ok": True, "id": jid}


@router.put("/{jid}")
async def update_journal(jid: int, body: JournalBody, user: Annotated[dict, Depends(get_current_user)]):
    await repository.update_journal(user["id"], jid, body.model_dump())
    return {"ok": True}


@router.delete("/{jid}")
async def delete_journal(jid: int, user: Annotated[dict, Depends(get_current_user)]):
    await repository.delete_journal(user["id"], jid)
    return {"ok": True}
