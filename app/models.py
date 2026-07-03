from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    """A login account. Admins are Teams with is_admin=True (no game state used)."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    ip_balance: Mapped[float] = mapped_column(Float, default=0.0)
    balance_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    current_region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True)
    current_region: Mapped["Region | None"] = relationship(foreign_keys=[current_region_id])


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)  # e.g. 0.70 for Rīga
    board_location: Mapped[str] = mapped_column(String(120), default="")
    held_by_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    held_by: Mapped["Team | None"] = relationship(foreign_keys=[held_by_team_id])


class Challenge(Base):
    """A challenge-card template belonging to a region's pool."""

    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    multiplier: Mapped[float] = mapped_column(Float, default=2.0)
    kind: Mapped[str] = mapped_column(String(20), default="normal")  # normal | steal (v2)

    region: Mapped[Region] = relationship()


class DailyChallenge(Base):
    """A challenge card made active for a given region on a given game day.
    Once any team completes it, it locks for that day (rule 4.8)."""

    __tablename__ = "daily_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_day: Mapped[int] = mapped_column(Integer, default=1)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_by_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    region: Mapped[Region] = relationship()
    challenge: Mapped[Challenge] = relationship()
    completed_by: Mapped["Team | None"] = relationship(foreign_keys=[completed_by_team_id])


class ChallengeAttempt(Base):
    """A team's bet on a daily challenge. Bet is deducted on submit; on success
    the team is credited bet * multiplier; on fail the bet is simply gone."""

    __tablename__ = "challenge_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    daily_challenge_id: Mapped[int] = mapped_column(ForeignKey("daily_challenges.id"))
    bet: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | success | fail
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped[Team] = relationship()
    daily_challenge: Mapped[DailyChallenge] = relationship()


class RegionDeposit(Base):
    """Accumulated deposit a team has staked on a region (rule 3). The team with
    the largest deposit holds the region. Deposits never refund."""

    __tablename__ = "region_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    team: Mapped[Team] = relationship()
    region: Mapped[Region] = relationship()


class LedgerEntry(Base):
    """Append-only financial history — the team's 'current financial situation'."""

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200))
    balance_after: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GameState(Base):
    """Singleton row (id=1) holding global game controls."""

    __tablename__ = "game_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    current_day: Mapped[int] = mapped_column(Integer, default=1)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
