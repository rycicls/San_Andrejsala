"""Helpers that mutate a team's balance and keep the ledger + decay consistent.

Golden rule: before any discrete money event (bet, payout, deposit, admin
adjust), call `settle()` first so continuous decay up to *now* is applied. Then
call `apply_delta()` for the discrete change. That keeps decay and events from
stepping on each other's timestamps.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import GameState, LedgerEntry, Region, Team
from .taxes import compute_rate


def _region_tax(region: Region | None) -> float:
    return region.tax_rate if region else 0.0


def income_per_min(team: Team, regions: list[Region], teams: list[Team]) -> float:
    """Rule 3.10 (Monopoly-style rent): a region's holder earns base_tax *
    region_tax per *other* team present in that region, per minute. The holder
    does not pay themselves. Sum across every region this team holds."""
    total = 0.0
    for r in regions:
        if r.held_by_team_id == team.id:
            present = sum(
                1
                for t in teams
                if t.current_region_id == r.id and not t.is_admin and t.id != team.id
            )
            total += settings.base_tax * r.tax_rate * present
    return total


def settle(
    team: Team, region: Region | None, running: bool, income: float = 0.0
) -> float:
    """Apply continuous decay (minus any region-capture income) to the team's
    balance up to now. Returns the gross decay rate (IP/min)."""
    now = datetime.now(timezone.utc)
    decay = compute_rate(team.ip_balance, _region_tax(region))
    net = decay - income  # positive = net loss; negative = net gain from holdings
    last = team.balance_updated_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if running and last is not None:
        elapsed_min = (now - last).total_seconds() / 60.0
        if elapsed_min > 0:
            team.ip_balance = max(0.0, team.ip_balance - net * elapsed_min)
    team.balance_updated_at = now
    return decay


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


def team_state(
    team: Team, region: Region | None, gs: GameState, income: float = 0.0
) -> dict:
    """The payload pushed over WebSocket / rendered for a team. `rate` is the NET
    change per minute the client should apply (positive = losing, negative =
    gaining thanks to region income)."""
    decay = compute_rate(team.ip_balance, _region_tax(region))
    return {
        "type": "state",
        "balance": round(team.ip_balance, 2),
        "rate": round(decay - income, 4),  # net; client subtracts this per min
        "decay": round(decay, 4),
        "income": round(income, 4),
        "running": bool(gs.running),
        "day": gs.current_day,
        "region": region.name if region else None,
    }
