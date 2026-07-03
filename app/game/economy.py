"""Helpers that mutate a team's balance and keep the ledger + decay consistent.

Golden rule: before any discrete money event (bet, payout, deposit, admin
adjust), call `settle()` first so continuous decay up to *now* is applied. Then
call `apply_delta()` for the discrete change. That keeps decay and events from
stepping on each other's timestamps.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GameState, LedgerEntry, Region, Team
from .taxes import compute_rate


def _region_tax(region: Region | None) -> float:
    return region.tax_rate if region else 0.0


def settle(team: Team, region: Region | None, running: bool) -> float:
    """Apply continuous decay to team.ip_balance up to now. Returns current rate."""
    now = datetime.now(timezone.utc)
    rate = compute_rate(team.ip_balance, _region_tax(region))
    last = team.balance_updated_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if running and last is not None:
        elapsed_min = (now - last).total_seconds() / 60.0
        if elapsed_min > 0:
            team.ip_balance = max(0.0, team.ip_balance - rate * elapsed_min)
    team.balance_updated_at = now
    return rate


def apply_delta(session: AsyncSession, team: Team, delta: float, reason: str) -> None:
    """Discrete balance change + an audit row. Balance is clamped at 0."""
    team.ip_balance = max(0.0, team.ip_balance + delta)
    session.add(
        LedgerEntry(
            team_id=team.id,
            delta=delta,
            reason=reason,
            balance_after=team.ip_balance,
        )
    )


def team_state(team: Team, region: Region | None, gs: GameState) -> dict:
    """The payload pushed over WebSocket / rendered for a team."""
    rate = compute_rate(team.ip_balance, _region_tax(region))
    return {
        "type": "state",
        "balance": round(team.ip_balance, 2),
        "rate": round(rate, 4),
        "running": bool(gs.running),
        "day": gs.current_day,
        "region": region.name if region else None,
    }
