import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.auth import decode_token
from backend.core.config import load_config
from backend.core.websocket import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    try:
        auth_message = await asyncio.wait_for(ws.receive_json(), timeout=5)
    except Exception:
        await ws.close(code=4001, reason="Authentication required")
        return

    if (
        not isinstance(auth_message, dict)
        or auth_message.get("type") != "auth"
        or not isinstance(auth_message.get("token"), str)
        or not auth_message["token"]
    ):
        await ws.close(code=4001, reason="Authentication required")
        return

    try:
        payload = decode_token(auth_message["token"])
        from backend.models import repository

        user = await repository.get_user_by_id(payload["sub"])
        if not user:
            await ws.close(code=4001, reason="Session expired")
            return
        if load_config().get("sso_enabled", True):
            if user.get("token_version") != payload.get("tv"):
                await ws.close(code=4001, reason="Session expired")
                return
    except Exception:
        await ws.close(code=4001, reason="Invalid token")
        return

    user_id = user["id"]
    ws_manager.register(ws, user_id)
    try:
        await ws.send_json({"type": "auth_ok"})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(ws, user_id)
