"""
FastAPI application factory.
"""

from __future__ import annotations
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from .routes import containers, backups, restore, jobs
from .middleware.auth import AuthMiddleware

log = logging.getLogger(__name__)

# Paths relative to project root
_BASE = Path(__file__).parent.parent
_STATIC_DIR = _BASE / "web" / "static"
_TEMPLATE_DIR = _BASE / "web" / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def create_app() -> FastAPI:
    app = FastAPI(
        title="Docker Backup Manager",
        description="Container backup and restore management",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # ── CORS (local dev — tighten for production) ──────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Auth middleware (stub — activates when config auth.enabled = true) ─────
    app.add_middleware(AuthMiddleware)

    # ── Static files ───────────────────────────────────────────────────────────
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── API routers ────────────────────────────────────────────────────────────
    app.include_router(containers.router, prefix="/api/containers", tags=["containers"])
    app.include_router(backups.router, prefix="/api/backups", tags=["backups"])
    app.include_router(restore.router, prefix="/api/restore", tags=["restore"])
    app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])

    # ── UI catch-all ───────────────────────────────────────────────────────────
    from fastapi import Request
    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
