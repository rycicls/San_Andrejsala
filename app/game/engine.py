"""Background loop: every TICK_SECONDS, apply decay to every active team and
push the fresh balance to their WebSocket connections."""

import asyncio

from sqlalchemy import select

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import GameState, Region, Team
from ..ws_manager import manager
from .economy import settle, team_state


async def _tick() -> None:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GameState, 1)
        if gs is None:
            return
        regions = {r.id: r for r in (await session.execute(select(Region))).scalars()}
        teams = list(
            (
                await session.execute(
                    select(Team).where(Team.is_admin == False, Team.active == True)  # noqa: E712
                )
            ).scalars()
        )
        for team in teams:
            region = regions.get(team.current_region_id)
            settle(team, region, gs.running)
        await session.commit()

        for team in teams:
            region = regions.get(team.current_region_id)
            await manager.send_team(team.id, team_state(team, region, gs))


async def game_loop() -> None:
    while True:
        await asyncio.sleep(settings.tick_seconds)
        try:
            await _tick()
        except Exception as exc:  # keep the loop alive no matter what
            print(f"[game_loop] tick error: {exc!r}")
