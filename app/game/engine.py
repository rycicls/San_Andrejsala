"""Background loop: every TICK_SECONDS, apply decay to every active team and
push the fresh balance to their WebSocket connections."""

import asyncio

from sqlalchemy import select

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import GameState, Region, RegionPresence, Team
from ..ws_manager import manager
from .economy import income_per_min, settle, team_state


async def _accumulate_presence(session, teams: list[Team]) -> None:
    """Add this tick's elapsed time to each team's presence in its current
    region (rule 3.2). Only called while the game is running."""
    rows = {
        (p.team_id, p.region_id): p
        for p in (await session.execute(select(RegionPresence))).scalars()
    }
    for team in teams:
        if not team.current_region_id:
            continue
        key = (team.id, team.current_region_id)
        p = rows.get(key)
        if p is None:
            p = RegionPresence(team_id=team.id, region_id=team.current_region_id, seconds=0.0)
            session.add(p)
        p.seconds += settings.tick_seconds


async def _tick() -> None:
    async with AsyncSessionLocal() as session:
        gs = await session.get(GameState, 1)
        if gs is None:
            return
        region_list = list((await session.execute(select(Region))).scalars())
        regions = {r.id: r for r in region_list}
        teams = list(
            (
                await session.execute(
                    select(Team).where(Team.is_admin == False, Team.active == True)  # noqa: E712
                )
            ).scalars()
        )
        # income depends on region occupancy, which is fixed within a tick
        incomes = {t.id: income_per_min(t, region_list, teams) for t in teams}
        for team in teams:
            region = regions.get(team.current_region_id)
            settle(team, region, gs.running, incomes[team.id])
        if gs.running:
            await _accumulate_presence(session, teams)
        await session.commit()

        for team in teams:
            region = regions.get(team.current_region_id)
            await manager.send_team(team.id, team_state(team, region, gs, incomes[team.id]))


async def game_loop() -> None:
    while True:
        await asyncio.sleep(settings.tick_seconds)
        try:
            await _tick()
        except Exception as exc:  # keep the loop alive no matter what
            print(f"[game_loop] tick error: {exc!r}")
