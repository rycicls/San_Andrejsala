from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..deps import current_team
from ..game.challenges import effective_multiplier
from ..game.regions import income_for_team
from ..game.taxes import compute_rate
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    JeopardyAttempt,
    JeopardyChallenge,
    KeyTaskAttempt,
    KeyTaskReveal,
    LedgerEntry,
    Region,
    RegionDayUnlock,
    RegionDeposit,
    RegionPresence,
    Team,
    TeamKeyUnlock,
)
from ..security import verify_password

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/")
async def index(team: Team | None = Depends(current_team)):
    if team is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    dest = "/admin" if team.is_admin else "/dashboard"
    return RedirectResponse(dest, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login")
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    team = await session.scalar(select(Team).where(Team.username == username))
    if team is None or not verify_password(password, team.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Nepareizs lietotājvārds vai parole"}
        )
    request.session["team_id"] = team.id
    dest = "/admin" if team.is_admin else "/dashboard"
    return RedirectResponse(dest, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard")
async def dashboard(
    request: Request,
    team: Team | None = Depends(current_team),
    session: AsyncSession = Depends(get_session),
):
    if team is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if team.is_admin:
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)

    gs = await session.get(GameState, 1)
    # eager-load held_by: async SQLAlchemy can't lazy-load in templates
    regions = list(
        (
            await session.execute(
                select(Region).options(selectinload(Region.held_by)).order_by(Region.name)
            )
        ).scalars()
    )
    region = next((r for r in regions if r.id == team.current_region_id), None)
    decay = compute_rate(team.ip_balance, region.tax_rate if region else 0.0)
    income = await income_for_team(session, team)

    # regions this team has unlocked (key task done) → gates capture betting
    unlocked_region_ids = set(
        (
            await session.execute(
                select(TeamKeyUnlock.region_id).where(TeamKeyUnlock.team_id == team.id)
            )
        ).scalars()
    )

    # the current region's key task + whether TODAY's first-blood is already gone.
    # rule 2.2: only load the task once an admin has revealed it TO THIS TEAM (or
    # the team already did it), so its text never reaches the page — view-source
    # included — for teams still on their way to the board.
    key_challenge = None
    first_blood_taken = False
    my_key_attempt = None
    if region is not None:
        revealed = (
            await session.scalar(
                select(KeyTaskReveal).where(
                    KeyTaskReveal.team_id == team.id, KeyTaskReveal.region_id == region.id
                )
            )
        ) is not None
        if revealed or region.id in unlocked_region_ids:
            key_challenge = await session.scalar(
                select(Challenge).where(
                    Challenge.region_id == region.id, Challenge.is_key == True  # noqa: E712
                )
            )
        first_blood_taken = (
            await session.scalar(
                select(RegionDayUnlock).where(
                    RegionDayUnlock.game_day == gs.current_day,
                    RegionDayUnlock.region_id == region.id,
                )
            )
        ) is not None
        # this team's latest claim for the region (any day) — gates the button below
        my_key_attempt = await session.scalar(
            select(KeyTaskAttempt)
            .where(
                KeyTaskAttempt.team_id == team.id,
                KeyTaskAttempt.region_id == region.id,
            )
            .order_by(KeyTaskAttempt.id.desc())
        )

    # other teams (steal targets)
    other_teams = list(
        (
            await session.execute(
                select(Team)
                .where(Team.is_admin == False, Team.id != team.id)  # noqa: E712
                .order_by(Team.name)
            )
        ).scalars()
    )

    # Daily cards — only visible once THIS team has done the region's key task.
    region_unlocked = region is not None and region.id in unlocked_region_ids
    dailies = []
    if region_unlocked:
        rows = list(
            (
                await session.execute(
                    select(DailyChallenge)
                    .options(selectinload(DailyChallenge.challenge))
                    .where(
                        DailyChallenge.game_day == gs.current_day,
                        DailyChallenge.region_id == region.id,
                    )
                    .order_by(DailyChallenge.id)
                )
            ).scalars()
        )
        for dc in rows:
            attempt = await session.scalar(
                select(ChallengeAttempt).where(
                    ChallengeAttempt.daily_challenge_id == dc.id,
                    ChallengeAttempt.team_id == team.id,
                )
            )
            dailies.append(
                {
                    "daily": dc,
                    "challenge": dc.challenge,
                    "attempt": attempt,
                    # rule 4.4: escalated by other teams' failures
                    "multiplier": await effective_multiplier(session, dc.id, dc.challenge),
                }
            )

    my_deposits = {
        d.region_id: d.amount
        for d in (
            await session.execute(
                select(RegionDeposit).where(RegionDeposit.team_id == team.id)
            )
        ).scalars()
    }
    # minutes spent in each region (rule 3.2 timer)
    region_minutes = {
        p.region_id: p.seconds / 60.0
        for p in (
            await session.execute(
                select(RegionPresence).where(RegionPresence.team_id == team.id)
            )
        ).scalars()
    }
    ledger = list(
        (
            await session.execute(
                select(LedgerEntry)
                .where(LedgerEntry.team_id == team.id)
                .order_by(LedgerEntry.id.desc())
                .limit(15)
            )
        ).scalars()
    )
    # Jeopardy (rule 7): available when broke; hide once a card is pending
    jeopardy_pending = await session.scalar(
        select(JeopardyAttempt).where(
            JeopardyAttempt.team_id == team.id, JeopardyAttempt.status == "pending"
        )
    )
    jeopardy_challenges = list(
        (
            await session.execute(select(JeopardyChallenge).order_by(JeopardyChallenge.value))
        ).scalars()
    )
    # the pre-authored challenge behind the picked card (shown while pending)
    jeopardy_current = None
    if jeopardy_pending is not None:
        jeopardy_current = next(
            (jc for jc in jeopardy_challenges if jc.value == jeopardy_pending.value), None
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "team": team,
            "gs": gs,
            "region": region,
            "regions": regions,
            "decay": decay,
            "income": income,
            "net_rate": decay - income,
            "dailies": dailies,
            "my_deposits": my_deposits,
            "region_minutes": region_minutes,
            "region_min_minutes": settings.region_min_minutes,
            "ledger": ledger,
            "broke": team.ip_balance <= 0,
            "jeopardy_pending": jeopardy_pending,
            "jeopardy_challenges": jeopardy_challenges,
            "jeopardy_current": jeopardy_current,
            "key_challenge": key_challenge,
            "first_blood_taken": first_blood_taken,
            "my_key_attempt": my_key_attempt,
            "region_unlocked": region_unlocked,
            "unlocked_region_ids": unlocked_region_ids,
            "other_teams": other_teams,
        },
    )


def _to_int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


@router.get("/admin")
async def admin_page(
    request: Request,
    f_region: str | None = None,
    f_day: str | None = None,
    team: Team | None = Depends(current_team),
    session: AsyncSession = Depends(get_session),
):
    if team is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not team.is_admin:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    filter_region = _to_int(f_region)  # None = all
    filter_day = _to_int(f_day)

    gs = await session.get(GameState, 1)
    regions = list((await session.execute(select(Region).order_by(Region.name))).scalars())
    region_by_id = {r.id: r for r in regions}
    teams = list(
        (
            await session.execute(
                select(Team).where(Team.is_admin == False).order_by(Team.name)  # noqa: E712
            )
        ).scalars()
    )

    # One unified approvals queue: key-task claims + challenge bets, oldest first.
    pending = []
    for a in (
        await session.execute(
            select(KeyTaskAttempt).where(KeyTaskAttempt.status == "pending")
        )
    ).scalars():
        region = region_by_id.get(a.region_id)
        pending.append(
            {
                "created_at": a.created_at,
                "team": await session.get(Team, a.team_id),
                "label": f"Atslēgas uzdevums — {region.name if region else '?'}",
                "detail": f"Diena {a.game_day}",
                "resolve_url": f"/admin/key-task/{a.id}/resolve",
            }
        )
    for a in (
        await session.execute(
            select(ChallengeAttempt).where(ChallengeAttempt.status == "pending")
        )
    ).scalars():
        daily = await session.get(DailyChallenge, a.daily_challenge_id)
        challenge = await session.get(Challenge, daily.challenge_id) if daily else None
        if challenge and challenge.kind == "steal":
            target = await session.get(Team, a.target_team_id) if a.target_team_id else None
            detail = (
                f"STEAL {int(challenge.steal_pct * 100)}% → "
                f"{target.name if target else '?'} · likme {a.bet:.0f}"
            )
        elif challenge:
            eff = await effective_multiplier(session, a.daily_challenge_id, challenge)
            grown = " ↑" if eff > challenge.multiplier else ""  # rule 4.4 escalated
            detail = f"x{eff:.2f}{grown} · likme {a.bet:.0f}"
        else:
            detail = f"likme {a.bet:.0f}"
        pending.append(
            {
                "created_at": a.created_at,
                "team": await session.get(Team, a.team_id),
                "label": challenge.title if challenge else "?",
                "detail": detail,
                "resolve_url": f"/admin/attempts/{a.id}/resolve",
            }
        )
    pending.sort(key=lambda x: x["created_at"])

    # the key task behind each region's task board (authored + deployed by admins)
    key_tasks = [
        {"challenge": c, "region": region_by_id.get(c.region_id)}
        for c in (
            await session.execute(
                select(Challenge)
                .where(Challenge.is_key == True)  # noqa: E712
                .order_by(Challenge.region_id)
            )
        ).scalars()
    ]

    # each region's key task — editable, and revealed PER TEAM (rule 2.2)
    revealed_pairs = {
        (r.team_id, r.region_id)
        for r in (await session.execute(select(KeyTaskReveal))).scalars()
    }
    key_tasks = sorted(
        (
            {
                "region": region_by_id.get(c.region_id),
                "challenge": c,
                "teams": [
                    {"team": t, "revealed": (t.id, c.region_id) in revealed_pairs}
                    for t in teams
                ],
            }
            for c in (
                await session.execute(
                    select(Challenge).where(Challenge.is_key == True)  # noqa: E712
                )
            ).scalars()
        ),
        key=lambda k: k["region"].name if k["region"] else "",
    )

    # editable Jeopardy challenges (one per value)
    jeopardy_challenges = list(
        (
            await session.execute(select(JeopardyChallenge).order_by(JeopardyChallenge.value))
        ).scalars()
    )
    jc_by_value = {jc.value: jc for jc in jeopardy_challenges}

    # broke teams' pending Jeopardy cards awaiting a ruling
    jeopardy = []
    for a in (
        await session.execute(
            select(JeopardyAttempt)
            .where(JeopardyAttempt.status == "pending")
            .order_by(JeopardyAttempt.id)
        )
    ).scalars():
        jc = jc_by_value.get(a.value)
        jeopardy.append(
            {
                "attempt": a,
                "team": await session.get(Team, a.team_id),
                "challenge": jc,
            }
        )

    # pool of challenges admins can assign to a day (non-key), grouped by region
    assignable = [
        {"challenge": c, "region": region_by_id.get(c.region_id)}
        for c in (
            await session.execute(
                select(Challenge)
                .where(Challenge.is_key == False)  # noqa: E712
                .order_by(Challenge.region_id, Challenge.title)
            )
        ).scalars()
    ]

    # current day→region challenge assignments (optionally filtered)
    assigned_q = (
        select(DailyChallenge)
        .options(selectinload(DailyChallenge.challenge))
        .order_by(DailyChallenge.game_day, DailyChallenge.region_id, DailyChallenge.id)
    )
    if filter_region is not None:
        assigned_q = assigned_q.where(DailyChallenge.region_id == filter_region)
    if filter_day is not None:
        assigned_q = assigned_q.where(DailyChallenge.game_day == filter_day)
    assigned = []
    for dc in (await session.execute(assigned_q)).scalars():
        n_bets = await session.scalar(
            select(func.count())
            .select_from(ChallengeAttempt)
            .where(ChallengeAttempt.daily_challenge_id == dc.id)
        )
        assigned.append(
            {
                "daily": dc,
                "challenge": dc.challenge,
                "region": region_by_id.get(dc.region_id),
                "has_bets": bool(n_bets),
            }
        )

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "team": team,
            "gs": gs,
            "regions": regions,
            "region_by_id": region_by_id,
            "teams": teams,
            "pending": pending,
            "key_tasks": key_tasks,
            "jeopardy": jeopardy,
            "key_tasks": key_tasks,
            "jeopardy_challenges": jeopardy_challenges,
            "assignable": assignable,
            "assigned": assigned,
            "filter_region": filter_region,
            "filter_day": filter_day,
            "total_days": settings.total_days,
        },
    )
