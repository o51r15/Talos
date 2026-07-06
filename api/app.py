"""
FastAPI application factory.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from .routes import containers, backups, restore, jobs
from .routes.config_route import router as config_router
from .middleware.auth import AuthMiddleware

log = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
_STATIC_DIR = _BASE / "web" / "static"
_TEMPLATE_DIR = _BASE / "web" / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop background services alongside the FastAPI app."""
    from core.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Docker Backup Manager",
        description="Container backup and restore management",
        version="0.3.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Auth middleware stub ───────────────────────────────────────────────────
    app.add_middleware(AuthMiddleware)

    # ── Static files ───────────────────────────────────────────────────────────
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(containers.router,  prefix="/api/containers", tags=["containers"])
    app.include_router(backups.router,     prefix="/api/backups",    tags=["backups"])
    app.include_router(restore.router,     prefix="/api/restore",    tags=["restore"])
    app.include_router(jobs.router,        prefix="/api/jobs",       tags=["jobs"])
    app.include_router(config_router,      prefix="/api/config",     tags=["config"])

    # ── UI ─────────────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/health")
    async def health():
        from core.scheduler import is_running, get_next_run
        return {
            "status": "ok",
            "scheduler_running": is_running(),
            "next_scheduled_backup": get_next_run(),
        }

    return app
