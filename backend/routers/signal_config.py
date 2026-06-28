import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends

from backend.core.auth import get_current_user
from backend.models import repository
from backend.services.signal_engine import DEFAULT_SIGNAL_CONFIG, get_merged_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signal-config", tags=["signal-config"])


@router.get("")
async def get_signal_config(user: Annotated[dict, Depends(get_current_user)]):
    saved = await repository.get_signal_config(user["id"])
    merged = get_merged_config(saved)
    return merged


@router.post("")
async def save_signal_config(data: Annotated[dict, Body()], user: Annotated[dict, Depends(get_current_user)]):
    logger.info(f"[signal_config] save user={user['id']} keys={list(data.keys()) if data else 'EMPTY'}")
    await repository.save_signal_config(user["id"], data)
    await repository.add_log(user["id"], user["username"], "update_config", "signal_config")
    return {"ok": True}


@router.post("/reset")
async def reset_signal_config(user: Annotated[dict, Depends(get_current_user)]):
    await repository.save_signal_config(user["id"], DEFAULT_SIGNAL_CONFIG)
    await repository.add_log(user["id"], user["username"], "update_config", "signal_config_reset")
    return {"ok": True, "config": DEFAULT_SIGNAL_CONFIG}
