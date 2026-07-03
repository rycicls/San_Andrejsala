import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import Base, engine
from .game.engine import game_loop
from .routers import admin, api, pages, ws
from .seed import seed

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v1: create tables directly. (For schema changes over time, switch to
    # Alembic migrations — see alembic.ini / README.)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed()

    task = asyncio.create_task(game_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="San Andrejsala", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(ws.router)
