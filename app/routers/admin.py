"""Admin control panel actions. All form-post + redirect back to /admin."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..deps import require_admin
from ..game.challenges import effective_multiplier
from ..game.economy import apply_delta, settle
from ..game.regions import income_for_team, reset_presence_on_move
from ..security import hash_password
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    JeopardyAttempt,
    JeopardyChallenge,
    KeyTaskAttempt,
    KeyTaskReveal,
    Region,
    RegionDayUnlock,
    Team,
    TeamKeyUnlock,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _back() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


async def _settle(session: AsyncSession, team: Team, gs: GameState) -> None:
    region = await session.get(Region, team.current_region_id) if team.current_region_id else None
    income = await income_for_team(session, team)
    settle(team, region, gs.running, income)


async def _award_first_blood(
    session: AsyncSession, region: Region, game_day: int, gs: GameState
) -> None:
    """The day's 50 IP goes to the first team that *did* the region's key task —
    the earliest CLAIM, not whichever the admin happens to approve first (rule 2.3
    'Pirmā komanda kura izdara'). The chance renews each day (rule 2.4); since a
    team may only ever do a region's key task once, each day it's contested by
    whoever hasn't unlocked that region yet.

    Walk the day's claims oldest-first: stop at any still-pending claim (we can't
    know yet whether it beats the later ones), skip rejected ones, and award to
    the first approved. Re-run on every resolution, so a rejection correctly hands
    the bonus down to the next-earliest approved claim.
    """
    already = await session.scalar(
        select(RegionDayUnlock).where(
            RegionDayUnlock.game_day == game_day,
            RegionDayUnlock.region_id == region.id,
        )
    )
    if already is not None:
        return  # this region/day's bonus is already settled

    claims = list(
        (
            await session.execute(
                select(KeyTaskAttempt)
                .where(
                    KeyTaskAttempt.region_id == region.id,
                    KeyTaskAttempt.game_day == game_day,
                )
                .order_by(KeyTaskAttempt.created_at, KeyTaskAttempt.id)
            )
        ).scalars()
    )
    for claim in claims:
        if claim.status == "pending":
            return  # an earlier claim is undecided — wait for it
        if claim.status == "success":
            winner = await session.get(Team, claim.team_id)
            try:
                async with session.begin_nested():
                    session.add(
                        RegionDayUnlock(
                            game_day=game_day,
                            region_id=region.id,
                            first_team_id=winner.id,
                        )
                    )
            except IntegrityError:
                return  # concurrent award already happened
            await _settle(session, winner, gs)
            apply_delta(
                session, winner, settings.key_task_bonus, f"Key task first-blood ({region.name})"
            )
            return
        # rejected -> keep walking to the next-earliest claim


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


@router.post("/teams/{team_id}/credentials")
async def update_team_credentials(
    team_id: int,
    name: str = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Rename a team / change its login. Blank password keeps the current one."""
    team = await session.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team")
    name, username = name.strip(), username.strip()
    if not name or not username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Vārds un lietotājvārds ir obligāti")
    clash = await session.scalar(
        select(Team).where(Team.username == username, Team.id != team_id)
    )
    if clash is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lietotājvārds jau aizņemts")
    team.name = name
    team.username = username
    if password.strip():
        team.password_hash = hash_password(password)
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
    await reset_presence_on_move(session, team, region_id or None)  # rule 3.2 timer resets
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

    # rule 4.8: only the first team completes a challenge — once it's locked by a
    # winner, any remaining attempts can only be marked failed.
    if result == "success" and daily.locked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Šo izaicinājumu jau izpildīja cita komanda"
        )

    attempt.status = "success" if result == "success" else "fail"
    attempt.resolved_at = datetime.now(timezone.utc)

    if attempt.status == "success":
        challenge = await session.get(Challenge, daily.challenge_id)
        await _settle(session, team, gs)
        if challenge.kind == "steal" and attempt.target_team_id:
            # refund the stake, then steal the same % of the target's current capital
            apply_delta(session, team, attempt.bet, f"Likme atgriezta: {challenge.title}")
            target = await session.get(Team, attempt.target_team_id)
            if target is not None:
                await _settle(session, target, gs)
                stolen = round(challenge.steal_pct * target.ip_balance, 2)
                apply_delta(session, target, -stolen, f"Aplaupīts ({challenge.title}): {team.name}")
                apply_delta(session, team, stolen, f"Nozagts no {target.name}: {challenge.title}")
        else:
            # rule 4.4: the card may have escalated from earlier teams' failures
            mult = await effective_multiplier(session, daily.id, challenge)
            payout = attempt.bet * mult  # bet returned WITH the multiplier
            apply_delta(session, team, payout, f"Uzvara: {challenge.title} (x{mult:.2f})")
        # rule 4.8: once completed it locks for everyone that day
        daily.locked = True
        daily.completed_by_team_id = team.id
    await session.commit()
    return _back()


@router.post("/key-task/{attempt_id}/resolve")
async def resolve_key_task(
    attempt_id: int,
    result: str = Form(...),  # success | fail
    session: AsyncSession = Depends(get_session),
):
    """Approve/reject a team's key-task claim. On success: grants the persistent
    region unlock (rule 3.1) and, if this team is the day's first successful
    claim, the first-blood bonus (rule 2.3)."""
    attempt = await session.get(KeyTaskAttempt, attempt_id)
    if attempt is None or attempt.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attempt not pending")
    gs = await session.get(GameState, 1)
    team = await session.get(Team, attempt.team_id)
    region = await session.get(Region, attempt.region_id)

    attempt.status = "success" if result == "success" else "fail"
    attempt.resolved_at = datetime.now(timezone.utc)

    if attempt.status == "success":
        # Persistent per-team unlock — rule 3.1: once, on any day, forever after.
        already = await session.scalar(
            select(TeamKeyUnlock).where(
                TeamKeyUnlock.team_id == team.id, TeamKeyUnlock.region_id == region.id
            )
        )
        if already is None:
            try:
                async with session.begin_nested():
                    session.add(TeamKeyUnlock(team_id=team.id, region_id=region.id))
            except IntegrityError:
                pass  # concurrent approval already unlocked it

    # (re)settle who gets this region's 50 IP for that day, by claim order
    await _award_first_blood(session, region, attempt.game_day, gs)

    await session.commit()
    return _back()


@router.post("/key-tasks/{cid}/edit")
async def edit_key_task(
    cid: int,
    title: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Author the region's key task (the one written on the task board)."""
    c = await session.get(Challenge, cid)
    if c is None or not c.is_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such key task")
    if not title.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nosaukums ir obligāts")
    c.title = title.strip()
    c.description = description
    await session.commit()
    return _back()


async def _get_key_task(session: AsyncSession, challenge_id: int) -> Challenge:
    kc = await session.get(Challenge, challenge_id)
    if kc is None or not kc.is_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such key task")
    return kc


@router.post("/key-tasks/{challenge_id}/edit")
async def edit_key_task(
    challenge_id: int,
    title: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Edit a region's key task text (what's on the board)."""
    kc = await _get_key_task(session, challenge_id)
    kc.title = title
    kc.description = description
    await session.commit()
    return _back()


@router.post("/key-tasks/{challenge_id}/deploy")
async def deploy_key_task(
    challenge_id: int,
    team_id: int = Form(...),
    action: str = Form(...),  # deploy | hide
    session: AsyncSession = Depends(get_session),
):
    """Rule 2.2: reveal a region's key task to ONE team — the one that just showed
    up at the board. Teams arrive at different times, so this is per team."""
    kc = await _get_key_task(session, challenge_id)
    team = await session.get(Team, team_id)
    if team is None or team.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such team")
    existing = await session.scalar(
        select(KeyTaskReveal).where(
            KeyTaskReveal.team_id == team.id, KeyTaskReveal.region_id == kc.region_id
        )
    )
    if action == "deploy":
        if existing is None:
            try:
                async with session.begin_nested():
                    session.add(KeyTaskReveal(team_id=team.id, region_id=kc.region_id))
            except IntegrityError:
                pass  # already revealed by a concurrent click
    elif existing is not None:
        await session.delete(existing)
    await session.commit()
    return _back()


@router.post("/jeopardy-challenge/{jc_id}/edit")
async def edit_jeopardy_challenge(
    jc_id: int,
    title: str = Form(...),
    description: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Edit the pre-authored challenge behind a Jeopardy value."""
    jc = await session.get(JeopardyChallenge, jc_id)
    if jc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such Jeopardy challenge")
    jc.title = title
    jc.description = description
    await session.commit()
    return _back()


@router.post("/jeopardy/{attempt_id}/resolve")
async def resolve_jeopardy(
    attempt_id: int,
    result: str = Form(...),  # success | fail
    session: AsyncSession = Depends(get_session),
):
    """Resolve a broke team's Jeopardy card (rule 7): success grants its value."""
    attempt = await session.get(JeopardyAttempt, attempt_id)
    if attempt is None or attempt.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attempt not pending")
    gs = await session.get(GameState, 1)
    team = await session.get(Team, attempt.team_id)
    attempt.status = "success" if result == "success" else "fail"
    attempt.resolved_at = datetime.now(timezone.utc)
    if attempt.status == "success":
        await _settle(session, team, gs)
        apply_delta(session, team, attempt.value, f"Jeopardy {int(attempt.value)}")
    await session.commit()
    return _back()
