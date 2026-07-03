"""Idempotent first-boot seed: regions, an admin, demo teams, sample challenges,
and the singleton GameState row. Safe to run every startup."""

from sqlalchemy import func, select

from .config import settings
from .database import AsyncSessionLocal
from .models import Challenge, GameState, Region, Team
from .security import hash_password

REGIONS = [
    ("riga", "Rīga", 0.70, "Rīgā"),
    ("kurzeme", "Kurzeme", 0.20, "Kuldīga"),
    ("latgale", "Latgale", 0.15, "Rēzekne"),
    ("vidzeme", "Vidzeme", 0.25, "Smiltene"),
    ("zemgale", "Zemgale", 0.30, "Jelgava"),
]

# A couple of sample cards per region so the UI has content on day one.
SAMPLE_CHALLENGES = {
    "riga": [
        ("Vecrīgas foto-orientēšanās", "Atrodiet un nofotografējiet 5 norādītos objektus.", 2.0),
        ("Trivia: Rīgas vēsture", "Atbildiet uz 10 jautājumiem bez AI/Google.", 1.5),
    ],
    "kurzeme": [
        ("Kuldīgas ķieģeļu tilts", "Uztaisiet komandas foto uz tilta.", 2.0),
        ("Vietējais gardums", "Nopērciet un noēdiet vietējo specialitāti.", 1.5),
    ],
    "latgale": [
        ("Rēzeknes Latgales māra", "Selfijs pie pieminekļa.", 2.0),
    ],
    "vidzeme": [
        ("Smiltenes izaicinājums", "Atrodiet augstāko punktu pilsētā.", 2.0),
    ],
    "zemgale": [
        ("Jelgavas pils", "Komandas foto pie pils.", 2.0),
    ],
}


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # GameState singleton
        if await session.get(GameState, 1) is None:
            session.add(GameState(id=1, current_day=1, running=False))

        # Regions
        existing_regions = {
            r.key: r for r in (await session.execute(select(Region))).scalars()
        }
        for key, name, tax, board in REGIONS:
            if key not in existing_regions:
                session.add(Region(key=key, name=name, tax_rate=tax, board_location=board))
        await session.commit()

        regions = {r.key: r for r in (await session.execute(select(Region))).scalars()}

        # Sample challenges (only if none exist yet)
        n_challenges = await session.scalar(select(func.count()).select_from(Challenge))
        if not n_challenges:
            for key, cards in SAMPLE_CHALLENGES.items():
                for title, desc, mult in cards:
                    session.add(
                        Challenge(
                            region_id=regions[key].id,
                            title=title,
                            description=desc,
                            multiplier=mult,
                        )
                    )

        # Admin
        admin_exists = await session.scalar(
            select(Team).where(Team.username == settings.admin_username)
        )
        if admin_exists is None:
            session.add(
                Team(
                    name="Administrators",
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    is_admin=True,
                )
            )

        # Demo teams
        n_teams = await session.scalar(
            select(func.count()).select_from(Team).where(Team.is_admin == False)  # noqa: E712
        )
        if not n_teams:
            for i in range(1, 6):
                session.add(
                    Team(
                        name=f"Komanda {i}",
                        username=f"team{i}",
                        password_hash=hash_password("changeme"),
                        ip_balance=settings.start_ip,
                    )
                )

        await session.commit()
