import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import Base, engine
from .game.engine import game_loop
from .routers import admin, api, pages, ws
from .seed import seed

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _back_target(request: Request) -> str:
    """Same-origin path to send a failed form post back to (avoids open redirect)."""
    ref = request.headers.get("referer")
    if ref:
        p = urlparse(ref)
        if p.netloc == request.url.netloc and p.path:
            return p.path
    return "/dashboard"


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


@app.exception_handler(StarletteHTTPException)
async def friendly_http_errors(request: Request, exc: StarletteHTTPException):
    """Turn rejected form submissions into a flash message + redirect back to the
    page, instead of dumping a raw JSON error at the player."""
    if request.method == "POST" and exc.status_code in (400, 403):
        request.session["flash"] = exc.detail
        return RedirectResponse(_back_target(request), status_code=303)
    if exc.status_code == 401:
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(admin.router)
app.include_router(ws.router)
