"""Admin control panel actions. All form-post + redirect back to /admin."""

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..deps import require_admin
from ..game.economy import apply_delta, settle
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    Region,
    Team,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

DRAW_COUNT = 4  # rule 2.5: 4 random tasks per region per day


def _back() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


async def _settle(session: AsyncSession, team: Team, gs: GameState) -> None:
    region = await session.get(Region, team.current_region_id) if team.current_region_id else None
    settle(team, region, gs.running)


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


@router.post("/draw")
async def draw_challenges(region_id: int = Form(...), session: AsyncSession = Depends(get_session)):
    """Draw up to 4 random challenge cards for a region on the current day.
    Idempotent per (day, region): clears any previous draw first."""
    gs = await session.get(GameState, 1)
    pool = list(
        (
            await session.execute(select(Challenge).where(Challenge.region_id == region_id))
        ).scalars()
    )
    if not pool:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No challenges in this region's pool")

    # remove existing draw for this day+region
    for dc in (
        await session.execute(
            select(DailyChallenge).where(
                DailyChallenge.game_day == gs.current_day,
                DailyChallenge.region_id == region_id,
            )
        )
    ).scalars():
        await session.delete(dc)

    picks = random.sample(pool, min(DRAW_COUNT, len(pool)))
    for ch in picks:
        session.add(
            DailyChallenge(game_day=gs.current_day, region_id=region_id, challenge_id=ch.id)
        )
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
        payout = attempt.bet * challenge.multiplier  # bet returned WITH the multiplier
        await _settle(session, team, gs)
        apply_delta(session, team, payout, f"Challenge win x{challenge.multiplier}")
        # rule 4.8: once completed it locks for everyone that day
        daily.locked = True
        daily.completed_by_team_id = team.id
    await session.commit()
    return _back()
