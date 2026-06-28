import json
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self._clients: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, user_id: int):
        await ws.accept()
        self._clients[user_id].add(ws)

    def disconnect(self, ws: WebSocket, user_id: int):
        self._clients[user_id].discard(ws)
        if not self._clients[user_id]:
            del self._clients[user_id]

    async def send_to_user(self, user_id: int, data: dict):
        if user_id not in self._clients:
            return
        dead: set[WebSocket] = set()
        msg = json.dumps(data, ensure_ascii=False)
        for ws in self._clients[user_id]:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._clients[user_id].difference_update(dead)

    async def broadcast(self, data: dict):
        dead_pairs: list[tuple[int, WebSocket]] = []
        msg = json.dumps(data, ensure_ascii=False)
        for user_id, clients in self._clients.items():
            for ws in clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead_pairs.append((user_id, ws))
        for user_id, ws in dead_pairs:
            self._clients[user_id].discard(ws)

    async def kick_user(self, user_id: int):
        if user_id not in self._clients:
            return
        msg = json.dumps({"type": "force_logout"}, ensure_ascii=False)
        for ws in list(self._clients[user_id]):
            try:
                await ws.send_text(msg)
                await ws.close(code=4002, reason="Logged in elsewhere")
            except Exception:
                pass
        self._clients.pop(user_id, None)

    @property
    def client_count(self) -> int:
        return sum(len(c) for c in self._clients.values())


ws_manager = WebSocketManager()
