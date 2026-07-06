"""
Retention and cleanup. Runs after every backup and on a scheduled basis.

Policy (from config):
  keep_last    — keep the N most recent archives per container per type
  max_age_days — delete anything older than N days (0 = disabled)

Snapshots in restore_snapshot_dir are NOT touched by retention.
"""

from __future__ import annotations
import os
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .models import BackupRecord, BackupType
from .config import get_config

log = logging.getLogger(__name__)


def run_retention(container_name: Optional[str] = None) -> int:
    """
    Apply retention policy. If container_name is given, only clean that
    container's backups. Otherwise clean all.
    Returns the number of files deleted.
    """
    cfg = get_config()
    backup_root = Path(cfg.backup_dest)
    deleted = 0

    if container_name:
        dirs = [backup_root / container_name]
    else:
        dirs = [d for d in backup_root.iterdir() if d.is_dir()]

    cutoff = (
        datetime.now() - timedelta(days=cfg.retention.max_age_days)
        if cfg.retention.max_age_days > 0
        else None
    )

    # Resolve once so the per-dir comparison is cheap and correct.
    # Name-only comparison would incorrectly skip any container named
    # the same as the last path component of restore_snapshot_dir.
    snap_dir_resolved = Path(cfg.restore_snapshot_dir).resolve()

    for container_dir in dirs:
        if not container_dir.exists():
            continue
        if container_dir.resolve() == snap_dir_resolved:
            continue
        deleted += _clean_container_dir(
            container_dir,
            cfg.retention.keep_last,
            cutoff,
        )

    if deleted:
        log.info(f"Retention: removed {deleted} old backup file(s)")
    return deleted


def _clean_container_dir(
    container_dir: Path,
    keep_last: int,
    cutoff: Optional[datetime],
) -> int:
    """Clean one container's backup directory."""
    # Group files by backup type
    by_type: Dict[str, List[BackupRecord]] = {t.value: [] for t in BackupType}

    for f in container_dir.iterdir():
        if not f.is_file():
            continue
        record = BackupRecord.from_path(str(f))
        if record:
            by_type.setdefault(record.backup_type.value, []).append(record)

    deleted = 0
    for btype, records in by_type.items():
        if not records:
            continue
        # Sort newest first
        records.sort(key=lambda r: r.timestamp, reverse=True)

        for i, record in enumerate(records):
            should_delete = False

            # Too old?
            if cutoff and record.timestamp < cutoff:
                should_delete = True
                log.debug(f"Retention (age): {record.filename}")

            # Beyond keep_last?
            if i >= keep_last:
                should_delete = True
                log.debug(f"Retention (count): {record.filename}")

            if should_delete:
                try:
                    os.remove(record.filepath)
                    deleted += 1
                    log.info(f"Deleted old backup: {record.filename}")
                except OSError as e:
                    log.warning(f"Could not delete {record.filepath}: {e}")

    return deleted



