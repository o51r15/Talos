"""
Job status routes.

GET /api/jobs                — recent jobs (all or filtered by container)
GET /api/jobs/active         — currently running jobs
GET /api/jobs/{id}           — single job status and log
"""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from typing import List, Optional

from core.models import Job
from core import jobs as job_store

router = APIRouter()


@router.get("", response_model=List[Job])
async def list_jobs(container: Optional[str] = None, limit: int = 50):
    """List recent jobs, optionally filtered by container name."""
    return job_store.list_jobs(container_name=container, limit=limit)


@router.get("/active", response_model=List[Job])
async def active_jobs():
    """Return all currently running or pending jobs."""
    return job_store.get_active_jobs()


@router.get("/{job_id}", response_model=Job)
async def get_job(job_id: str):
    """Return a single job by ID."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job
