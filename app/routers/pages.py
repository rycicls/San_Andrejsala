from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..deps import current_team
from ..game.taxes import compute_rate
from ..models import (
    Challenge,
    ChallengeAttempt,
    DailyChallenge,
    GameState,
    LedgerEntry,
    Region,
    RegionDeposit,
    Team,
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
    rate = compute_rate(team.ip_balance, region.tax_rate if region else 0.0)

    # Daily cards for this team's region + day, with the team's attempt (if any).
    dailies = []
    if region is not None:
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
            dailies.append({"daily": dc, "challenge": dc.challenge, "attempt": attempt})

    my_deposits = {
        d.region_id: d.amount
        for d in (
            await session.execute(
                select(RegionDeposit).where(RegionDeposit.team_id == team.id)
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

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "team": team,
            "gs": gs,
            "region": region,
            "regions": regions,
            "rate": rate,
            "dailies": dailies,
            "my_deposits": my_deposits,
            "ledger": ledger,
        },
    )


@router.get("/admin")
async def admin_page(
    request: Request,
    team: Team | None = Depends(current_team),
    session: AsyncSession = Depends(get_session),
):
    if team is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not team.is_admin:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

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

    pending_rows = list(
        (
            await session.execute(
                select(ChallengeAttempt)
                .where(ChallengeAttempt.status == "pending")
                .order_by(ChallengeAttempt.id)
            )
        ).scalars()
    )
    pending = []
    for a in pending_rows:
        daily = await session.get(DailyChallenge, a.daily_challenge_id)
        challenge = await session.get(Challenge, daily.challenge_id) if daily else None
        pending.append(
            {
                "attempt": a,
                "team": await session.get(Team, a.team_id),
                "challenge": challenge,
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
        },
    )
