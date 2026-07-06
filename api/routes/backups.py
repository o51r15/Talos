"""
Backup routes.

GET  /api/backups                      — all backups grouped by container
GET  /api/backups/{container}          — backup history for one container
POST /api/backups/{container}          — trigger a backup job
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import List, Dict

from core.models import BackupRecord, BackupOptions, Job
from core.backup_index import list_backups_for_container, list_all_backups
from core import docker_client as dc
from core.compose_discovery import enrich_with_compose
from core import jobs as job_store

router = APIRouter()


@router.get("", response_model=Dict[str, List[BackupRecord]])
async def all_backups(include_snapshots: bool = False):
    """Return all backups grouped by container name."""
    return list_all_backups(include_snapshots=include_snapshots)


@router.get("/{container_name}", response_model=List[BackupRecord])
async def container_backups(container_name: str, include_snapshots: bool = False):
    """Return backup history for a single container."""
    return list_backups_for_container(container_name, include_snapshots=include_snapshots)


@router.post("/{container_name}", response_model=Job)
async def trigger_backup(container_name: str, options: BackupOptions):
    """
    Trigger a backup job for the named container.
    Returns the Job object immediately; poll /api/jobs/{id} for status.
    """
    container = dc.get_container(container_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")

    if container.is_self:
        raise HTTPException(status_code=400, detail="Cannot back up the backup manager itself")

    # Enrich with compose info so backup engine can find compose files
    containers = enrich_with_compose([container])
    container = containers[0]

    job = job_store.submit_backup(container, options)
    return job
