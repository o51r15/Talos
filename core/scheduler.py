"""
Scheduled backup runner using APScheduler.

Activated when config.schedule.enabled = true (or SCHEDULE_ENABLED=true env var).
Runs on the cron expression in config.schedule.cron.

The scheduler is attached to the FastAPI lifespan so it starts and stops
cleanly with the web server. In CLI mode it is not used.
"""

from __future__ import annotations
import logging
from typing import Optional

log = logging.getLogger(__name__)

_scheduler = None   # APScheduler instance, lazy-initialised


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    """Initialise and start the scheduler if enabled in config."""
    global _scheduler
    from core.config import get_config
    cfg = get_config()

    if not cfg.schedule.enabled:
        log.info("Scheduled backups disabled (schedule.enabled = false)")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error(
            "apscheduler not installed — scheduled backups unavailable. "
            "Run: pip install apscheduler"
        )
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    try:
        trigger = CronTrigger.from_crontab(cfg.schedule.cron, timezone="UTC")
    except ValueError as e:
        log.error(
            f"Invalid cron expression '{cfg.schedule.cron}': {e} — "
            "scheduler NOT started. Fix schedule.cron in config and reload."
        )
        _scheduler = None
        return
    _scheduler.add_job(
        _run_scheduled_backup,
        trigger=trigger,
        id="scheduled_backup",
        name="Scheduled full backup",
        replace_existing=True,
        misfire_grace_time=300,   # allow 5 min late start
    )
    _scheduler.start()
    log.info(f"Scheduler started — cron: '{cfg.schedule.cron}' (UTC)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("Scheduler stopped")
    _scheduler = None


def get_next_run() -> Optional[str]:
    """Return ISO timestamp of the next scheduled run, or None."""
    if not _scheduler or not _scheduler.running:
        return None
    job = _scheduler.get_job("scheduled_backup")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


# ── Job ────────────────────────────────────────────────────────────────────────

def _run_scheduled_backup() -> None:
    """
    Called by APScheduler on the configured cron schedule.
    Runs in a background thread — does not block the web server.
    """
    from core.config import get_config
    from core import docker_client as dc
    from core.compose_discovery import enrich_with_compose
    from core.models import BackupOptions
    from core.backup_engine import run_backup
    from core.retention import run_retention

    cfg = get_config()
    sched = cfg.schedule
    log.info("Scheduled backup starting")

    options = BackupOptions(
        backup_data=sched.backup_data,
        backup_compose=sched.backup_compose,
        backup_internal=sched.backup_internal,
        include_compose_siblings=False,   # schedule always does all containers anyway
    )

    containers = dc.list_containers(all_containers=True)
    containers = enrich_with_compose(containers)

    backed_up = 0
    skipped = 0
    failed = 0

    for c in containers:
        if c.is_self:
            skipped += 1
            continue
        if sched.skip_stopped and c.status.value != "running":
            log.debug(f"Scheduled backup: skipping stopped container {c.name}")
            skipped += 1
            continue

        try:
            records = run_backup(c, options)
            run_retention(c.name)
            backed_up += 1
            log.info(f"Scheduled backup OK: {c.name} ({len(records)} archive(s))")
        except Exception as e:
            failed += 1
            log.error(f"Scheduled backup FAILED: {c.name} — {e}")

    log.info(
        f"Scheduled backup complete — "
        f"backed up: {backed_up}, skipped: {skipped}, failed: {failed}"
    )
