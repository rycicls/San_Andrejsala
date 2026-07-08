from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    kind: Mapped[str] = mapped_column(String(20), default="normal")  # normal | steal
    # steal: bet = steal_pct of your capital; on success steal that % of a target's capital
    steal_pct: Mapped[float] = mapped_column(Float, default=0.0)
    # the region's key task (rule 2): first team to do it each day gets a bonus + triggers the draw
    is_key: Mapped[bool] = mapped_column(Boolean, default=False)

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
    # for steal challenges: which team we're trying to steal from
    target_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped[Team] = relationship(foreign_keys=[team_id])
    target_team: Mapped["Team | None"] = relationship(foreign_keys=[target_team_id])
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


class RegionDayUnlock(Base):
    """Tracks, per region per day, the first team to complete the key task
    (rule 2.3 first-blood bonus) and that the daily draw has been triggered."""

    __tablename__ = "region_day_unlocks"
    __table_args__ = (UniqueConstraint("game_day", "region_id", name="uq_regionday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_day: Mapped[int] = mapped_column(Integer)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    first_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)

    region: Mapped[Region] = relationship()
    first_team: Mapped["Team | None"] = relationship()


class TeamKeyUnlock(Base):
    """A team has completed a region's key task at least once (rule 3.1 gate for
    placing region-capture deposits). Persists across days — this is also what
    makes challenges stay visible on later days (rule 2.8 carry-over)."""

    __tablename__ = "team_key_unlocks"
    __table_args__ = (UniqueConstraint("team_id", "region_id", name="uq_teamkey"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))


class JeopardyChallenge(Base):
    """The pre-authored, harder challenge behind each Jeopardy value (100–500).
    Difficulty scales with the IP reward. Admins edit these in the panel."""

    __tablename__ = "jeopardy_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[float] = mapped_column(Float, unique=True)  # 100, 200, ... 500
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")


class JeopardyAttempt(Base):
    """Rule 7: a team that hits 0 IP can pick a Jeopardy card (100–500) to earn
    IP back. Admin resolves it like a challenge (success grants the value)."""

    __tablename__ = "jeopardy_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    value: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | success | fail
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped[Team] = relationship()


class RegionPresence(Base):
    """Accumulated time (seconds, while the game is running) a team has spent in
    a region. Gates region-capture betting: rule 3.2 requires 30 min present."""

    __tablename__ = "region_presence"
    __table_args__ = (UniqueConstraint("team_id", "region_id", name="uq_presence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"))
    seconds: Mapped[float] = mapped_column(Float, default=0.0)


class GameState(Base):
    """Singleton row (id=1) holding global game controls."""

    __tablename__ = "game_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    current_day: Mapped[int] = mapped_column(Integer, default=1)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
