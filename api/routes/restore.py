"""
Restore routes.

GET  /api/restore/{container}/snapshots  — list safety snapshots for a container
POST /api/restore/{container}            — trigger a restore job
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import List

from core.models import RestoreOptions, BackupRecord, Job
from core.restore_engine import list_restore_snapshots
from core import docker_client as dc
from core.compose_discovery import enrich_with_compose
from core import jobs as job_store

router = APIRouter()


@router.get("/{container_name}/snapshots", response_model=List[BackupRecord])
async def restore_snapshots(container_name: str):
    """List available safety snapshots for a container."""
    return list_restore_snapshots(container_name)


@router.post("/{container_name}", response_model=Job)
async def trigger_restore(container_name: str, options: RestoreOptions):
    """
    Trigger a restore job for the named container.

    The client should populate options.backup_id_data / backup_id_compose /
    backup_id_internal with the filename of the backup to restore from.

    Returns the Job immediately; poll /api/jobs/{id} for status.
    """
    if not any([options.restore_data, options.restore_compose, options.restore_internal]):
        raise HTTPException(
            status_code=400,
            detail="At least one of restore_data, restore_compose, or restore_internal must be true",
        )

    # Each enabled restore type must have a backup selected — otherwise the
    # engine would silently skip it and report success having done nothing.
    missing = []
    if options.restore_data and not options.backup_id_data:
        missing.append("backup_id_data")
    if options.restore_compose and not options.backup_id_compose:
        missing.append("backup_id_compose")
    if options.restore_internal and not options.backup_id_internal:
        missing.append("backup_id_internal")
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Restore type enabled but no backup selected: {', '.join(missing)}",
        )

    container = dc.get_container(container_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")

    if container.is_self:
        raise HTTPException(status_code=400, detail="Cannot restore the backup manager itself")

    # Enrich with compose info
    containers = enrich_with_compose([container])
    container = containers[0]

    job = job_store.submit_restore(container, options)
    return job
