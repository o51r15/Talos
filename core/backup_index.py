"""
Backup index. Scans the backup destination directory and returns structured
lists of BackupRecord objects. Used by the API and CLI to show backup history.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import List, Optional, Dict

from .models import BackupRecord
from .config import get_config

log = logging.getLogger(__name__)


def list_backups_for_container(
    container_name: str,
    include_snapshots: bool = False,
) -> List[BackupRecord]:
    """Return all backups for a single container, newest first."""
    cfg = get_config()
    records = _scan_dir(Path(cfg.backup_dest) / container_name)

    if include_snapshots:
        snap_dir = Path(cfg.restore_snapshot_dir) / container_name
        for r in _scan_dir(snap_dir):
            r.is_restore_snapshot = True
            records.append(r)

    records.sort(key=lambda r: r.timestamp, reverse=True)
    return records


def list_all_backups(include_snapshots: bool = False) -> Dict[str, List[BackupRecord]]:
    """Return all backups grouped by container name."""
    cfg = get_config()
    result: Dict[str, List[BackupRecord]] = {}

    backup_root = Path(cfg.backup_dest)
    if not backup_root.exists():
        return result

    for container_dir in backup_root.iterdir():
        if not container_dir.is_dir():
            continue
        # Skip the restore_snapshot dir if it lives inside backup_dest
        if container_dir.resolve() == Path(cfg.restore_snapshot_dir).resolve():
            continue
        name = container_dir.name
        records = list_backups_for_container(name, include_snapshots=include_snapshots)
        if records:
            result[name] = records

    return result


def get_latest_backup(container_name: str, backup_type: Optional[str] = None) -> Optional[BackupRecord]:
    """Return the most recent backup for a container, optionally filtered by type."""
    records = list_backups_for_container(container_name)
    if backup_type:
        records = [r for r in records if r.backup_type.value == backup_type]
    return records[0] if records else None


# ── Internal ───────────────────────────────────────────────────────────────────

def _scan_dir(directory: Path) -> List[BackupRecord]:
    if not directory.exists():
        return []
    records = []
    for f in directory.iterdir():
        if f.is_file():
            r = BackupRecord.from_path(str(f))
            if r:
                records.append(r)
    return records
