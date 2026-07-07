"""Team-facing actions. All use HTML form posts and redirect back to the
dashboard (Post/Redirect/Get) so the site works without any JS framework."""

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..deps import require_team
from ..game.economy import apply_delta, settle
from ..game.regions import get_deposit, income_for_team, recompute_holder
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    Region,
    RegionDayUnlock,
    Team,
    TeamKeyUnlock,
)

router = APIRouter()

MIN_DEPOSIT_STEP = 25.0


def _back() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


async def _settle_team(session: AsyncSession, team: Team) -> None:
    """Freeze decay (net of region-capture income) up to now."""
    gs = await session.get(GameState, 1)
    region = await session.get(Region, team.current_region_id) if team.current_region_id else None
    income = await income_for_team(session, team)
    settle(team, region, bool(gs and gs.running), income)


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


@router.post("/key-task")
async def complete_key_task(
    team: Team = Depends(require_team),
    session: AsyncSession = Depends(get_session),
):
    """Complete the current region's key task (rule 2). Doing it reveals the
    region's challenges for the day and unlocks region-capture betting (rule 3.1).
    The first team to do it each day also gets a first-blood bonus (rule 2.3)."""
    region_id = team.current_region_id
    if not region_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set your region first")
    region = await session.get(Region, region_id)
    key = await session.scalar(
        select(Challenge).where(
            Challenge.region_id == region_id, Challenge.is_key == True  # noqa: E712
        )
    )
    if key is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This region has no key task")
    gs = await session.get(GameState, 1)

    # persistent per-team unlock (capture-betting gate)
    already = await session.scalar(
        select(TeamKeyUnlock).where(
            TeamKeyUnlock.team_id == team.id, TeamKeyUnlock.region_id == region_id
        )
    )
    if already is None:
        session.add(TeamKeyUnlock(team_id=team.id, region_id=region_id))

    # first team this day → bonus + trigger the draw
    day_unlock = await session.scalar(
        select(RegionDayUnlock).where(
            RegionDayUnlock.game_day == gs.current_day,
            RegionDayUnlock.region_id == region_id,
        )
    )
    if day_unlock is None:
        session.add(
            RegionDayUnlock(
                game_day=gs.current_day, region_id=region_id, first_team_id=team.id
            )
        )
        await _settle_team(session, team)
        apply_delta(session, team, settings.key_task_bonus, f"Key task first-blood ({region.name})")

    await session.commit()
    return _back()


@router.post("/challenges/{daily_id}/bet")
async def bet_challenge(
    daily_id: int,
    amount: float = Form(0.0),
    target_team_id: int | None = Form(None),
    team: Team = Depends(require_team),
    session: AsyncSession = Depends(get_session),
):
    daily = await session.get(DailyChallenge, daily_id)
    if daily is None or daily.locked:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Challenge unavailable")
    # can't bet on a region's challenges until you've done its key task (rule 2.1)
    unlocked = await session.scalar(
        select(TeamKeyUnlock).where(
            TeamKeyUnlock.team_id == team.id, TeamKeyUnlock.region_id == daily.region_id
        )
    )
    if unlocked is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Complete the region's key task first")
    challenge = await session.get(Challenge, daily.challenge_id)
    await _settle_team(session, team)

    if challenge.kind == "steal":
        # rule 4.2: the bet is a fixed % of YOUR capital; you pick a victim
        if not target_team_id or target_team_id == team.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a target team to steal from")
        target = await session.get(Team, target_team_id)
        if target is None or target.is_admin:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid target team")
        bet = round(challenge.steal_pct * team.ip_balance, 2)
        if bet <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No capital to stake")
        apply_delta(session, team, -bet, f"Steal stake vs {target.name}")
        session.add(
            ChallengeAttempt(
                team_id=team.id,
                daily_challenge_id=daily.id,
                bet=bet,
                target_team_id=target_team_id,
                status="pending",
            )
        )
    else:
        if amount <= 0 or amount > team.ip_balance:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bet must be >0 and <= your IP")
        apply_delta(session, team, -amount, f"Bet on challenge #{daily.challenge_id}")
        session.add(
            ChallengeAttempt(
                team_id=team.id, daily_challenge_id=daily.id, bet=amount, status="pending"
            )
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
    # rule 3.1: must have completed this region's key task at least once
    unlocked = await session.scalar(
        select(TeamKeyUnlock).where(
            TeamKeyUnlock.team_id == team.id, TeamKeyUnlock.region_id == region_id
        )
    )
    if unlocked is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Complete this region's key task before betting on it"
        )
    # rule 3.9: 25 IP step
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
