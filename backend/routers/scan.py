from typing import Annotated

from fastapi import APIRouter, Depends

from backend.core.auth import get_current_user
from backend.services.scanner import manual_scan

router = APIRouter(prefix="/api/scan", tags=["scan"])


@router.post("")
async def trigger_scan(user: Annotated[dict, Depends(get_current_user)]):
    signals = await manual_scan(user["id"])
    return {"ok": True, "signals": signals}
