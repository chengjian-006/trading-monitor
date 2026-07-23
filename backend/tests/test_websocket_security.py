import asyncio

from fastapi import WebSocketDisconnect

from backend.models import repository
from backend.routers import ws as ws_router


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = iter(messages)
        self.accepted = False
        self.sent = []
        self.closed = []

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        return next(self._messages)

    async def receive_text(self):
        raise WebSocketDisconnect()

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code, reason):
        self.closed.append((code, reason))


class _FakeManager:
    def __init__(self):
        self.registered = []
        self.disconnected = []

    def register(self, websocket, user_id):
        self.registered.append((websocket, user_id))

    def disconnect(self, websocket, user_id):
        self.disconnected.append((websocket, user_id))


def test_websocket_authenticates_with_first_message_without_url_token(monkeypatch):
    websocket = _FakeWebSocket([{"type": "auth", "token": "signed-jwt"}])
    manager = _FakeManager()

    async def fake_get_user(user_id):
        return {"id": user_id, "username": "alice", "role": "user", "token_version": 3}

    monkeypatch.setattr(ws_router, "decode_token", lambda token: {"sub": 17, "tv": 3})
    monkeypatch.setattr(ws_router, "load_config", lambda: {"sso_enabled": False})
    monkeypatch.setattr(ws_router, "ws_manager", manager)
    monkeypatch.setattr(repository, "get_user_by_id", fake_get_user)

    asyncio.run(ws_router.websocket_endpoint(websocket))

    assert websocket.accepted is True
    assert manager.registered == [(websocket, 17)]
    assert websocket.sent == [{"type": "auth_ok"}]
    assert websocket.closed == []


def test_websocket_rejects_stale_database_token_version(monkeypatch):
    websocket = _FakeWebSocket([{"type": "auth", "token": "stale-jwt"}])
    manager = _FakeManager()

    async def fake_get_user(user_id):
        return {"id": user_id, "username": "alice", "role": "user", "token_version": 4}

    monkeypatch.setattr(ws_router, "decode_token", lambda token: {"sub": 17, "tv": 3})
    monkeypatch.setattr(ws_router, "load_config", lambda: {"sso_enabled": True})
    monkeypatch.setattr(ws_router, "ws_manager", manager)
    monkeypatch.setattr(repository, "get_user_by_id", fake_get_user)

    asyncio.run(ws_router.websocket_endpoint(websocket))

    assert websocket.accepted is True
    assert websocket.closed[0][0] == 4001
    assert manager.registered == []


def test_websocket_requires_auth_as_first_message(monkeypatch):
    websocket = _FakeWebSocket([{"type": "ping"}])
    manager = _FakeManager()
    monkeypatch.setattr(ws_router, "ws_manager", manager)

    asyncio.run(ws_router.websocket_endpoint(websocket))

    assert websocket.accepted is True
    assert websocket.closed[0][0] == 4001
    assert manager.registered == []
