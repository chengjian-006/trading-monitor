from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from backend.core.auth import decode_token
from backend.core.config import load_config
from backend.core.websocket import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(default="")):
    if not token:
        await ws.close(code=4001, reason="Missing token")
        return
    try:
        payload = decode_token(token)
    except Exception:
        await ws.close(code=4001, reason="Invalid token")
        return

    # 与 get_current_user 同口径校验 token_version: 否则改密/踢下线后旧 token 仍能连 WS 收推送,
    # 会话吊销在 WS 通道被绕过。sso_enabled 时比对 DB token_version, 不符即拒。
    if load_config().get("sso_enabled", True):
        try:
            from backend.models import repository
            db_tv = await repository.get_token_version(payload["sub"])
            if payload.get("tv") and db_tv != payload["tv"]:
                await ws.close(code=4001, reason="Session expired")
                return
        except Exception:
            await ws.close(code=4001, reason="Auth check failed")
            return

    user_id = payload["sub"]
    await ws_manager.connect(ws, user_id)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws, user_id)
