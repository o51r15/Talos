"""
Container routes.

GET  /api/containers          — list all containers with status and compose info
GET  /api/containers/{name}   — single container detail
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import List

from core.models import ContainerInfo
from core import docker_client as dc
from core.compose_discovery import enrich_with_compose
from core.backup_index import get_latest_backup, list_backups_for_container

router = APIRouter()


@router.get("", response_model=List[ContainerInfo])
async def list_containers():
    """Return all containers enriched with compose and backup metadata."""
    containers = dc.list_containers(all_containers=True)
    containers = enrich_with_compose(containers)
    _attach_backup_meta(containers)
    return containers


@router.get("/{name}", response_model=ContainerInfo)
async def get_container(name: str):
    """Return detail for a single container."""
    container = dc.get_container(name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container '{name}' not found")
    containers = enrich_with_compose([container])
    _attach_backup_meta(containers)
    return containers[0]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _attach_backup_meta(containers: List[ContainerInfo]) -> None:
    """Attach last_backup and backup_count from the backup index."""
    for c in containers:
        records = list_backups_for_container(c.name)
        c.backup_count = len(records)
        if records:
            c.last_backup = records[0].timestamp   # already sorted newest-first
