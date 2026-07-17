"""Challenge scoring (rule 4.4)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Challenge, ChallengeAttempt

# rule 4.4: each failed attempt raises the card by 50% of its ORIGINAL multiplier
FAIL_ESCALATION = 0.5


async def effective_multiplier(
    session: AsyncSession, daily_id: int, challenge: Challenge
) -> float:
    """Rule 4.4: 'Ja komanda neveiksmīgi izpilda izaicinājumu, tad tas palielinās
    par 50%, no tā sākotnējā reizinātāja — izņemot steal izaicinājumiem.'

    Derived from the number of failed attempts on this day's card rather than
    stored, so it needs no schema change and resets naturally each day (a new day
    means a new DailyChallenge row). Steal cards never escalate.
    """
    if challenge.kind == "steal":
        return challenge.multiplier
    fails = await session.scalar(
        select(func.count())
        .select_from(ChallengeAttempt)
        .where(
            ChallengeAttempt.daily_challenge_id == daily_id,
            ChallengeAttempt.status == "fail",
        )
    )
    return challenge.multiplier * (1 + FAIL_ESCALATION * (fails or 0))
