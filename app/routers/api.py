"""Team-facing actions. All use HTML form posts and redirect back to the
dashboard (Post/Redirect/Get) so the site works without any JS framework."""

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import require_team
from ..game.economy import apply_delta, settle
from ..game.regions import get_deposit, recompute_holder
from ..models import ChallengeAttempt, DailyChallenge, GameState, Region, Team

router = APIRouter()

MIN_DEPOSIT_STEP = 25.0


def _back() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


async def _settle_team(session: AsyncSession, team: Team) -> None:
    gs = await session.get(GameState, 1)
    region = await session.get(Region, team.current_region_id) if team.current_region_id else None
    settle(team, region, bool(gs and gs.running))


@router.post("/set-region")
async def set_region(
    region_id: int = Form(...),
    team: Team = Depends(require_team),
    session: AsyncSession = Depends(get_session),
):
    await _settle_team(session, team)  # freeze decay under old region first
    team.current_region_id = region_id or None
    await session.commit()
    return _back()


@router.post("/challenges/{daily_id}/bet")
async def bet_challenge(
    daily_id: int,
    amount: float = Form(...),
    team: Team = Depends(require_team),
    session: AsyncSession = Depends(get_session),
):
    daily = await session.get(DailyChallenge, daily_id)
    if daily is None or daily.locked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Challenge unavailable")
    await _settle_team(session, team)
    if amount <= 0 or amount > team.ip_balance:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bet must be >0 and <= your IP")

    apply_delta(session, team, -amount, f"Bet on challenge #{daily.challenge_id}")
    session.add(
        ChallengeAttempt(team_id=team.id, daily_challenge_id=daily.id, bet=amount, status="pending")
    )
    await session.commit()
    return _back()


@router.post("/regions/{region_id}/deposit")
async def deposit_region(
    region_id: int,
    amount: float = Form(...),
    team: Team = Depends(require_team),
    session: AsyncSession = Depends(get_session),
):
    region = await session.get(Region, region_id)
    if region is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such region")
    # v1 relaxations: we skip the "unlocked key task" + "30 min in region" gates
    # (senate-adjudicated IRL). We still enforce the 25 IP step from rule 3.9.
    if amount < MIN_DEPOSIT_STEP or amount % MIN_DEPOSIT_STEP != 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Deposit must be a multiple of 25 IP")
    await _settle_team(session, team)
    if amount > team.ip_balance:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not enough IP")

    apply_delta(session, team, -amount, f"Deposit on {region.name}")
    dep = await get_deposit(session, team.id, region_id)
    dep.amount += amount
    await session.flush()
    await recompute_holder(session, region_id)
    await session.commit()
    return _back()
