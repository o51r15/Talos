"""
In-memory job tracker. Backup and restore operations run in a thread pool
so they don't block the FastAPI event loop. Jobs are tracked here and
exposed via the /api/jobs routes.

Jobs are held in memory — they reset on restart. A future phase can
persist them to a JSON file or SQLite if history across restarts is needed.
"""

from __future__ import annotations
import uuid
import logging
import concurrent.futures
from datetime import datetime
from typing import Dict, Optional, List, Callable

from .models import Job, JobStatus, JobType, ContainerInfo, BackupOptions, RestoreOptions

log = logging.getLogger(__name__)

# Global job store and thread pool
_jobs: Dict[str, Job] = {}
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


# ── Public API ─────────────────────────────────────────────────────────────────

def submit_backup(
    container: ContainerInfo,
    options: BackupOptions,
) -> Job:
    """Submit a backup job. Returns the Job immediately (runs in background)."""
    job = Job(
        id=str(uuid.uuid4()),
        job_type=JobType.BACKUP,
        container_name=container.name,
        options=options.model_dump(),
    )
    _jobs[job.id] = job
    _executor.submit(_run_backup_job, job.id, container, options)
    return job


def submit_restore(
    container: ContainerInfo,
    options: RestoreOptions,
) -> Job:
    """Submit a restore job. Returns the Job immediately (runs in background)."""
    job = Job(
        id=str(uuid.uuid4()),
        job_type=JobType.RESTORE,
        container_name=container.name,
        options=options.model_dump(),
    )
    _jobs[job.id] = job
    _executor.submit(_run_restore_job, job.id, container, options)
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def list_jobs(container_name: Optional[str] = None, limit: int = 50) -> List[Job]:
    jobs = list(_jobs.values())
    if container_name:
        jobs = [j for j in jobs if j.container_name == container_name]
    jobs.sort(key=lambda j: j.started_at or datetime.min, reverse=True)
    return jobs[:limit]


def get_active_jobs() -> List[Job]:
    return [j for j in _jobs.values() if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]


# ── Job runners ────────────────────────────────────────────────────────────────

def _run_backup_job(job_id: str, container: ContainerInfo, options: BackupOptions) -> None:
    from .backup_engine import run_backup
    from .retention import run_retention

    job = _jobs[job_id]
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow()
    job.add_log(f"Starting backup for {container.name}")

    def _log_cb(msg: str, level: str = "info") -> None:
        job.add_log(msg, level)

    try:
        records = run_backup(container, options, log_cb=_log_cb)
        job.add_log(f"Backup produced {len(records)} archive(s)")

        # Apply retention after successful backup
        removed = run_retention(container.name)
        if removed:
            job.add_log(f"Retention: removed {removed} old archive(s)")

        job.status = JobStatus.SUCCESS
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.add_log(f"Backup failed: {e}", "error")
        log.exception(f"Backup job {job_id} failed")
    finally:
        job.completed_at = datetime.utcnow()


def _run_restore_job(job_id: str, container: ContainerInfo, options: RestoreOptions) -> None:
    from .restore_engine import run_restore

    job = _jobs[job_id]
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow()
    job.add_log(f"Starting restore for {container.name}")

    def _log_cb(msg: str, level: str = "info") -> None:
        job.add_log(msg, level)

    try:
        success = run_restore(container, options, log_cb=_log_cb)
        job.status = JobStatus.SUCCESS if success else JobStatus.FAILED
        if not success:
            job.error = "Restore returned failure — check log for details"
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.add_log(f"Restore failed: {e}", "error")
        log.exception(f"Restore job {job_id} failed")
    finally:
        job.completed_at = datetime.utcnow()
