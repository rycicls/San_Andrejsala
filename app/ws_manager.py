from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    """Tracks live WebSocket connections per team so the game loop and request
    handlers can push updates. One process, in-memory — fine for ~20 users.
    (If you ever run >1 Uvicorn worker, this needs a Redis pub/sub backplane.)"""

    def __init__(self) -> None:
        self.active: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, team_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self.active[team_id].add(ws)

    def disconnect(self, team_id: int, ws: WebSocket) -> None:
        self.active[team_id].discard(ws)
        if not self.active[team_id]:
            self.active.pop(team_id, None)

    async def send_team(self, team_id: int, message: dict) -> None:
        for ws in list(self.active.get(team_id, ())):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(team_id, ws)

    async def broadcast(self, message: dict) -> None:
        for team_id in list(self.active.keys()):
            await self.send_team(team_id, message)


manager = ConnectionManager()
