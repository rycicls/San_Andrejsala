from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Region, RegionDeposit, RegionPresence, Team


async def presence_minutes(session: AsyncSession, team_id: int, region_id: int) -> float:
    """How long (minutes) a team has been in a region — the rule 3.2 timer.
    This is *continuous* presence in the current region; it resets on leaving."""
    p = await session.scalar(
        select(RegionPresence).where(
            RegionPresence.team_id == team_id, RegionPresence.region_id == region_id
        )
    )
    return (p.seconds / 60.0) if p else 0.0


async def reset_presence_on_move(session: AsyncSession, team: Team, new_region_id: int | None) -> None:
    """The rule 3.2 timer counts continuous presence, so switching regions wipes
    it: zero out the region being left and (re)start the one being entered at 0."""
    if team.current_region_id == new_region_id:
        return  # not actually moving
    for rid in {team.current_region_id, new_region_id}:
        if not rid:
            continue
        p = await session.scalar(
            select(RegionPresence).where(
                RegionPresence.team_id == team.id, RegionPresence.region_id == rid
            )
        )
        if p:
            p.seconds = 0.0


async def income_for_team(session: AsyncSession, team: Team) -> float:
    """Region-capture income (IP/min) for a team, computed from the DB. Used by
    request handlers; the engine tick computes the same thing from memory."""
    held = list(
        (
            await session.execute(select(Region).where(Region.held_by_team_id == team.id))
        ).scalars()
    )
    total = 0.0
    for r in held:
        present = await session.scalar(
            select(func.count())
            .select_from(Team)
            .where(
                Team.current_region_id == r.id,
                Team.is_admin == False,  # noqa: E712
                Team.id != team.id,  # holder doesn't pay themselves (Monopoly rent)
            )
        )
        total += settings.base_tax * r.tax_rate * (present or 0)
    return total


async def get_deposit(session: AsyncSession, team_id: int, region_id: int) -> RegionDeposit:
    dep = await session.scalar(
        select(RegionDeposit).where(
            RegionDeposit.team_id == team_id, RegionDeposit.region_id == region_id
        )
    )
    if dep is None:
        dep = RegionDeposit(team_id=team_id, region_id=region_id, amount=0.0)
        session.add(dep)
    return dep


async def recompute_holder(session: AsyncSession, region_id: int) -> None:
    """Region is held by the team with the strictly-largest deposit; ties => neutral."""
    region = await session.get(Region, region_id)
    if region is None:
        return
    deposits = list(
        (
            await session.execute(
                select(RegionDeposit).where(RegionDeposit.region_id == region_id)
            )
        ).scalars()
    )
    if not deposits:
        region.held_by_team_id = None
        return
    top = max(deposits, key=lambda d: d.amount)
    tied = [d for d in deposits if d.amount == top.amount]
    region.held_by_team_id = None if (len(tied) > 1 or top.amount <= 0) else top.team_id
