from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Region, RegionDeposit, Team


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
            .where(Team.current_region_id == r.id, Team.is_admin == False)  # noqa: E712
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
