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

# The key task per region (rule 2): first team to do it each day gets the bonus
# and triggers that region's 4-card draw.
KEY_TASKS = {
    "riga": ("Atslēga: Brīvības piemineklis", "Komandas foto pie Brīvības pieminekļa."),
    "kurzeme": ("Atslēga: Kuldīgas ūdenskritums", "Komandas foto pie Ventas rumbas."),
    "latgale": ("Atslēga: Rēzeknes pils", "Komandas foto pie pilsdrupām."),
    "vidzeme": ("Atslēga: Smiltenes centrs", "Komandas foto pie baznīcas."),
    "zemgale": ("Atslēga: Jelgavas pils", "Komandas foto pie pils galvenās ieejas."),
}

# Normal cards drawn into the daily pool (title, desc, multiplier).
SAMPLE_CHALLENGES = {
    "riga": [
        ("Vecrīgas foto-orientēšanās", "Atrodiet un nofotografējiet 5 norādītos objektus.", 2.0),
        ("Trivia: Rīgas vēsture", "Atbildiet uz 10 jautājumiem bez AI/Google.", 1.5),
        ("Ielu mūziķis", "Nopelniet 1 EUR uzstājoties uz ielas.", 3.0),
    ],
    "kurzeme": [
        ("Kuldīgas ķieģeļu tilts", "Uztaisiet komandas foto uz tilta.", 2.0),
        ("Vietējais gardums", "Nopērciet un noēdiet vietējo specialitāti.", 1.5),
    ],
    "latgale": [
        ("Rēzeknes Latgales māra", "Selfijs pie pieminekļa.", 2.0),
        ("Keramikas meklējumi", "Atrodiet Latgales keramiku.", 1.5),
    ],
    "vidzeme": [
        ("Smiltenes izaicinājums", "Atrodiet augstāko punktu pilsētā.", 2.0),
        ("Dabas taka", "Noejiet 2 km pārgājienu taku.", 1.5),
    ],
    "zemgale": [
        ("Jelgavas pils", "Komandas foto pie pils.", 2.0),
        ("Cukurfabrika", "Atrodiet bijušās cukurfabrikas vietu.", 1.5),
    ],
}

# Steal cards (title, desc, steal_pct). Bet = that % of your capital; on success
# you steal the same % of a chosen team's capital.
STEAL_CHALLENGES = {
    "riga": [("Aplaupīšana", "Nozodziet 50% no izvēlētās komandas kapitāla.", 0.50)],
    "zemgale": [("Zemgales reids", "Nozodziet 30% no izvēlētās komandas kapitāla.", 0.30)],
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

        # Challenges (only if none exist yet): key task + normal + steal per region
        n_challenges = await session.scalar(select(func.count()).select_from(Challenge))
        if not n_challenges:
            for key, region in regions.items():
                if key in KEY_TASKS:
                    title, desc = KEY_TASKS[key]
                    session.add(
                        Challenge(region_id=region.id, title=title, description=desc, is_key=True)
                    )
                for title, desc, mult in SAMPLE_CHALLENGES.get(key, []):
                    session.add(
                        Challenge(
                            region_id=region.id, title=title, description=desc, multiplier=mult
                        )
                    )
                for title, desc, pct in STEAL_CHALLENGES.get(key, []):
                    session.add(
                        Challenge(
                            region_id=region.id,
                            title=title,
                            description=desc,
                            kind="steal",
                            steal_pct=pct,
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
