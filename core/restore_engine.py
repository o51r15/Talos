"""
Restore engine.

Sequence for a full restore:
  1. Take a safety snapshot (backup current state to restore_snapshot_dir)
  2. Stop the container
  3. Delete current data / compose / volumes as selected
  4. Extract selected backup archive(s) to their original locations
  5. Restart the container
  6. Optionally purge the safety snapshot (config: purge_snapshot_on_success)

Partial restores (data-only, compose-only, internal-only) are supported
by setting the corresponding flags in RestoreOptions.
"""

from __future__ import annotations
import os
import shutil
import tarfile
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List

from .models import RestoreOptions, BackupOptions, ContainerInfo, BackupRecord
from .config import get_config
from .backup_engine import run_backup
from . import docker_client as dc

log = logging.getLogger(__name__)

LogCallback = Callable[[str, str], None]


# ── Public entry point ─────────────────────────────────────────────────────────

def run_restore(
    container: ContainerInfo,
    options: RestoreOptions,
    log_cb: Optional[LogCallback] = None,
) -> bool:
    """
    Execute a restore for one container.
    Returns True on success, False on failure.
    The caller (job runner) should not raise — we return status here.
    """
    def _log(msg: str, level: str = "info") -> None:
        log.log(getattr(logging, level.upper(), logging.INFO), msg)
        if log_cb:
            log_cb(msg, level)

    cfg = get_config()
    snapshot_records: List[BackupRecord] = []

    # ── 1. Safety snapshot ─────────────────────────────────────────────────────
    _log("Taking safety snapshot before restore...")
    try:
        snapshot_opts = BackupOptions(
            backup_data=options.restore_data,
            backup_compose=options.restore_compose,
            backup_internal=options.restore_internal,
        )
        snapshot_records = _take_safety_snapshot(container, snapshot_opts, _log)
        _log(f"Safety snapshot complete: {len(snapshot_records)} archive(s)")
    except Exception as e:
        _log(f"Safety snapshot failed: {e}", "error")
        _log("Aborting restore — current data was not snapshotted", "error")
        return False

    was_running = container.status.value == "running"

    # ── 2. Stop container ──────────────────────────────────────────────────────
    if was_running:
        _log(f"Stopping container: {container.name}")
        if not dc.stop_container(container.name):
            _log("Failed to stop container — aborting restore", "error")
            return False

    success = False
    try:
        # ── 3 & 4. Wipe and restore ────────────────────────────────────────────
        if options.restore_data and options.backup_id_data:
            _log("Restoring data directory...")
            ok = _restore_data(container, options.backup_id_data, _log)
            if not ok:
                raise RuntimeError("Data restore failed")

        if options.restore_compose and options.backup_id_compose:
            _log("Restoring compose file(s)...")
            ok = _restore_compose(container, options.backup_id_compose, _log)
            if not ok:
                raise RuntimeError("Compose restore failed")

        if options.restore_internal and options.backup_id_internal:
            _log("Restoring internal container data...")
            ok = _restore_internal(container, options.backup_id_internal, _log)
            if not ok:
                raise RuntimeError("Internal data restore failed")

        success = True
        _log("Restore complete ✓")

    except Exception as e:
        _log(f"Restore error: {e}", "error")
        _log("Container was left stopped — check state before restarting", "warning")

    finally:
        # ── 5. Restart ─────────────────────────────────────────────────────────
        if was_running and success:
            _log(f"Starting container: {container.name}")
            dc.start_container(container.name)

        # ── 6. Purge snapshot if configured ────────────────────────────────────
        if success and cfg.purge_snapshot_on_success and snapshot_records:
            _log("Purging safety snapshot (purge_snapshot_on_success = true)")
            _purge_snapshot_records(snapshot_records, _log)

    return success


# ── Safety snapshot ────────────────────────────────────────────────────────────

def _take_safety_snapshot(
    container: ContainerInfo,
    options: BackupOptions,
    _log: LogCallback,
) -> List[BackupRecord]:
    """Run a backup but redirect output to the restore_snapshot_dir."""
    cfg = get_config()
    snapshot_dir = Path(cfg.restore_snapshot_dir) / container.name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Temporarily monkey-patch the backup destination so run_backup
    # writes to snapshot_dir. We do this by calling the backup primitives
    # directly rather than modifying config.
    from .backup_engine import (
        _backup_data_dir,
        _backup_compose,
        _backup_internal,
    )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    records: List[BackupRecord] = []

    if options.backup_data and container.data_dir:
        r = _backup_data_dir(container, snapshot_dir, timestamp, _log)
        if r:
            r.is_restore_snapshot = True
            records.append(r)

    if options.backup_compose and container.compose and container.compose.config_files:
        r = _backup_compose(container, snapshot_dir, timestamp, _log)
        if r:
            r.is_restore_snapshot = True
            records.append(r)

    if options.backup_internal and container.has_internal_volumes:
        r = _backup_internal(container, snapshot_dir, timestamp, _log)
        if r:
            r.is_restore_snapshot = True
            records.append(r)

    return records


# ── Restore steps ──────────────────────────────────────────────────────────────

def _restore_data(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    cfg = get_config()
    backup_path = Path(cfg.backup_dest) / container.name / backup_id

    if not backup_path.exists():
        _log(f"Backup file not found: {backup_path}", "error")
        return False

    data_dir = Path(container.data_dir)
    if not data_dir.exists():
        _log(f"Data directory does not exist: {data_dir}", "warning")
        data_dir.mkdir(parents=True, exist_ok=True)
    else:
        _log(f"Clearing data directory: {data_dir}")
        for item in data_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    _log(f"Extracting {backup_id} -> {data_dir}")
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            # Strip the top-level directory name (which is the container name)
            members = tar.getmembers()
            for member in members:
                parts = Path(member.name).parts
                if len(parts) > 1:
                    member.name = str(Path(*parts[1:]))
                elif len(parts) == 1 and parts[0] == data_dir.name:
                    continue  # skip the top-level dir entry itself
                tar.extract(member, path=data_dir.parent)
        _log("Data extraction complete")
        return True
    except Exception as e:
        _log(f"Extraction failed: {e}", "error")
        return False


def _restore_compose(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    cfg = get_config()
    backup_path = Path(cfg.backup_dest) / container.name / backup_id

    if not backup_path.exists():
        _log(f"Compose backup not found: {backup_path}", "error")
        return False

    if not container.compose or not container.compose.working_dir:
        _log("No compose working directory known — cannot restore compose", "error")
        return False

    working_dir = Path(container.compose.working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Extracting compose archive to {working_dir}")
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(path=working_dir)
        _log("Compose extraction complete")
        return True
    except Exception as e:
        _log(f"Compose extraction failed: {e}", "error")
        return False


def _restore_internal(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    cfg = get_config()
    backup_path = Path(cfg.backup_dest) / container.name / backup_id

    if not backup_path.exists():
        _log(f"Internal backup not found: {backup_path}", "error")
        return False

    _log(f"Loading image from {backup_id}")
    image_id = dc.load_image(str(backup_path))
    if not image_id:
        _log("docker load failed", "error")
        return False

    _log(f"Image loaded: {image_id[:12]} — container must be recreated from this image to apply")
    _log("NOTE: internal restore loads the image; recreate the container manually or via compose", "warning")
    return True


# ── Snapshot cleanup ───────────────────────────────────────────────────────────

def _purge_snapshot_records(records: List[BackupRecord], _log: LogCallback) -> None:
    for r in records:
        try:
            if os.path.exists(r.filepath):
                os.remove(r.filepath)
                _log(f"Purged snapshot: {r.filename}")
        except Exception as e:
            _log(f"Could not purge snapshot {r.filename}: {e}", "warning")


def list_restore_snapshots(container_name: str) -> List[BackupRecord]:
    """List safety snapshots available for a container."""
    cfg = get_config()
    snap_dir = Path(cfg.restore_snapshot_dir) / container_name
    if not snap_dir.exists():
        return []
    records = []
    for f in snap_dir.iterdir():
        if f.is_file():
            r = BackupRecord.from_path(str(f))
            if r:
                r.is_restore_snapshot = True
                records.append(r)
    return sorted(records, key=lambda x: x.timestamp, reverse=True)
