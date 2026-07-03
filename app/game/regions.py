from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Region, RegionDeposit


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
