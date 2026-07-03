from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..game.economy import team_state
from ..models import GameState, Region, Team
from ..ws_manager import manager

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # The session cookie rides along with the WS handshake, so SessionMiddleware
    # has already populated ws.session for us.
    team_id = ws.session.get("team_id")
    if team_id is None:
        await ws.close(code=1008)
        return

    await manager.connect(team_id, ws)
    try:
        # Push one immediate snapshot so the UI isn't blank until the next tick.
        async with AsyncSessionLocal() as session:
            team = await session.get(Team, team_id)
            gs = await session.get(GameState, 1)
            if team and gs and not team.is_admin:
                region = (
                    await session.get(Region, team.current_region_id)
                    if team.current_region_id
                    else None
                )
                await ws.send_json(team_state(team, region, gs))
        # Keep the socket open; we don't expect inbound messages in v1.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(team_id, ws)
