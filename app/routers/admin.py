"""Admin control panel actions. All form-post + redirect back to /admin."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..deps import require_admin
from ..game.economy import apply_delta, settle
from ..game.regions import income_for_team
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    Region,
    Team,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _back() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


async def _settle(session: AsyncSession, team: Team, gs: GameState) -> None:
    region = await session.get(Region, team.current_region_id) if team.current_region_id else None
    income = await income_for_team(session, team)
    settle(team, region, gs.running, income)


@router.post("/game")
async def toggle_game(action: str = Form(...), session: AsyncSession = Depends(get_session)):
    gs = await session.get(GameState, 1)
    if action == "start":
        # Reset every team's decay anchor to now so paused time isn't charged.
        gs.running = True
        now = datetime.now(timezone.utc)
        for team in (await session.execute(select(Team))).scalars():
            team.balance_updated_at = now
    elif action == "pause":
        for team in (
            await session.execute(select(Team).where(Team.is_admin == False))  # noqa: E712
        ).scalars():
            await _settle(session, team, gs)
        gs.running = False
    await session.commit()
    return _back()


@router.post("/day")
async def next_day(session: AsyncSession = Depends(get_session)):
    """Advance the day and grant every team the daily IP (rule 3)."""
    gs = await session.get(GameState, 1)
    if gs.current_day >= settings.total_days:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Spēlei ir tikai {settings.total_days} dienas")
    gs.current_day += 1
    for team in (
        await session.execute(select(Team).where(Team.is_admin == False))  # noqa: E712
    ).scalars():
        await _settle(session, team, gs)
        apply_delta(session, team, settings.daily_ip, f"Dienas {gs.current_day} IP")
    await session.commit()
    return _back()


@router.post("/teams/{team_id}/adjust")
async def adjust_team(
    team_id: int,
    delta: float = Form(...),
    reason: str = Form("Admin adjustment"),
    session: AsyncSession = Depends(get_session),
):
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team")
    gs = await session.get(GameState, 1)
    await _settle(session, team, gs)
    apply_delta(session, team, delta, reason or "Admin adjustment")
    await session.commit()
    return _back()


@router.post("/teams/{team_id}/region")
async def set_team_region(
    team_id: int,
    region_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team")
    gs = await session.get(GameState, 1)
    await _settle(session, team, gs)
    team.current_region_id = region_id or None
    await session.commit()
    return _back()


@router.post("/challenges/create")
async def create_challenge(
    region_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    kind: str = Form("normal"),  # normal | steal
    multiplier: float = Form(2.0),
    steal_pct: float = Form(0.0),
    session: AsyncSession = Depends(get_session),
):
    """Author a new challenge definition in a region's pool (not yet on any day)."""
    if not await session.get(Region, region_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such region")
    session.add(
        Challenge(
            region_id=region_id,
            title=title,
            description=description,
            kind="steal" if kind == "steal" else "normal",
            multiplier=multiplier,
            steal_pct=steal_pct if kind == "steal" else 0.0,
        )
    )
    await session.commit()
    return _back()


@router.post("/daily/assign")
async def assign_daily(
    challenge_id: int = Form(...),
    game_day: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """Put a challenge on a specific day for its region (admins pre-set these)."""
    challenge = await session.get(Challenge, challenge_id)
    if challenge is None or challenge.is_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a normal/steal challenge")
    existing = await session.scalar(
        select(DailyChallenge).where(
            DailyChallenge.game_day == game_day,
            DailyChallenge.region_id == challenge.region_id,
            DailyChallenge.challenge_id == challenge_id,
        )
    )
    if existing is None:
        session.add(
            DailyChallenge(
                game_day=game_day, region_id=challenge.region_id, challenge_id=challenge_id
            )
        )
    await session.commit()
    return _back()


@router.post("/daily/{daily_id}/remove")
async def remove_daily(daily_id: int, session: AsyncSession = Depends(get_session)):
    """Un-assign a challenge from a day. Blocked once a team has bet on it."""
    daily = await session.get(DailyChallenge, daily_id)
    if daily is None:
        return _back()
    has_bet = await session.scalar(
        select(func.count())
        .select_from(ChallengeAttempt)
        .where(ChallengeAttempt.daily_challenge_id == daily_id)
    )
    if has_bet:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Teams have already bet on this challenge")
    await session.delete(daily)
    await session.commit()
    return _back()


@router.post("/attempts/{attempt_id}/resolve")
async def resolve_attempt(
    attempt_id: int,
    result: str = Form(...),  # success | fail
    session: AsyncSession = Depends(get_session),
):
    attempt = await session.get(ChallengeAttempt, attempt_id)
    if attempt is None or attempt.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attempt not pending")
    gs = await session.get(GameState, 1)
    daily = await session.get(DailyChallenge, attempt.daily_challenge_id)
    team = await session.get(Team, attempt.team_id)

    attempt.status = "success" if result == "success" else "fail"
    attempt.resolved_at = datetime.now(timezone.utc)

    if attempt.status == "success":
        challenge = await session.get(Challenge, daily.challenge_id)
        await _settle(session, team, gs)
        if challenge.kind == "steal" and attempt.target_team_id:
            # refund the stake, then steal the same % of the target's current capital
            apply_delta(session, team, attempt.bet, "Steal stake returned")
            target = await session.get(Team, attempt.target_team_id)
            if target is not None:
                await _settle(session, target, gs)
                stolen = round(challenge.steal_pct * target.ip_balance, 2)
                apply_delta(session, target, -stolen, f"Robbed by {team.name}")
                apply_delta(session, team, stolen, f"Stole from {target.name}")
        else:
            payout = attempt.bet * challenge.multiplier  # bet returned WITH the multiplier
            apply_delta(session, team, payout, f"Challenge win x{challenge.multiplier}")
        # rule 4.8: once completed it locks for everyone that day
        daily.locked = True
        daily.completed_by_team_id = team.id
    await session.commit()
    return _back()
