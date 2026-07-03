from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import Team


async def current_team(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Team | None:
    team_id = request.session.get("team_id")
    if team_id is None:
        return None
    return await session.get(Team, team_id)


async def require_team(team: Team | None = Depends(current_team)) -> Team:
    if team is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    return team


async def require_admin(team: Team = Depends(require_team)) -> Team:
    if not team.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return team
