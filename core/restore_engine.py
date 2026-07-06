"""
Restore engine.

Sequence for a full restore:
  1. Take a safety snapshot (backup current state to restore_snapshot_dir)
  2. Stop the container
  3. Wipe selected data paths
  4. Extract selected backup archive(s) to their original locations
  5. Restart the container
  6. Optionally purge the safety snapshot (config: purge_snapshot_on_success)

Partial restores (data-only, compose-only, internal-only) are supported.

Backup ID resolution:
  Backup IDs are filenames. They are resolved first against
  {backup_dest}/{container}/ and then against
  {restore_snapshot_dir}/{container}/ — so safety snapshots are
  restorable exactly like regular backups.

Internal restore:
  The client passes any one filename from the target snapshot set as
  backup_id_internal. The engine finds ALL internal files in the same
  directory sharing that timestamp, restores each named volume via
  busybox, and handles legacy docker-save .tar files.
"""

from __future__ import annotations
import os
import shutil
import tarfile
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List

from .models import RestoreOptions, BackupOptions, ContainerInfo, BackupRecord, BackupType
from .config import get_config
from . import docker_client as dc

log = logging.getLogger(__name__)

LogCallback = Callable[[str, str], None]

# Python 3.12+ tarfile extraction filter — blocks path traversal, device
# nodes, and absolute paths. On older Pythons the kwarg doesn't exist.
_EXTRACT_KWARGS = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}


# ── Public entry point ─────────────────────────────────────────────────────────

def run_restore(
    container: ContainerInfo,
    options: RestoreOptions,
    log_cb: Optional[LogCallback] = None,
) -> bool:
    """
    Execute a restore for one container.
    Returns True on success, False on failure.
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
        _log("Aborting restore — current state was not snapshotted", "error")
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
            if not _restore_data(container, options.backup_id_data, _log):
                raise RuntimeError("Data restore failed")

        if options.restore_compose and options.backup_id_compose:
            _log("Restoring compose file(s)...")
            if not _restore_compose(container, options.backup_id_compose, _log):
                raise RuntimeError("Compose restore failed")

        if options.restore_internal and options.backup_id_internal:
            _log("Restoring internal volumes...")
            if not _restore_internal(container, options.backup_id_internal, _log):
                raise RuntimeError("Internal restore failed")

        success = True
        _log("Restore complete ✓")

    except Exception as e:
        _log(f"Restore error: {e}", "error")
        _log("Container left stopped — verify state before restarting manually", "warning")

    finally:
        # ── 5. Restart ─────────────────────────────────────────────────────────
        if was_running and success:
            _log(f"Starting container: {container.name}")
            dc.start_container(container.name)

        # ── 6. Purge snapshot ──────────────────────────────────────────────────
        if success and cfg.purge_snapshot_on_success and snapshot_records:
            _log("Purging safety snapshot (purge_snapshot_on_success = true)")
            _purge_snapshot_records(snapshot_records, _log)

    return success


# ── Backup ID resolution ───────────────────────────────────────────────────────

def _resolve_backup_path(container_name: str, backup_id: str) -> Optional[Path]:
    """
    Resolve a backup filename to a full path.
    Checks the normal backup directory first, then the safety snapshot
    directory — so restore snapshots are restorable like any other backup.
    """
    cfg = get_config()
    for base in (cfg.backup_dest, cfg.restore_snapshot_dir):
        candidate = Path(base) / container_name / backup_id
        if candidate.is_file():
            return candidate
    return None


# ── Safety snapshot ────────────────────────────────────────────────────────────

def _take_safety_snapshot(
    container: ContainerInfo,
    options: BackupOptions,
    _log: LogCallback,
) -> List[BackupRecord]:
    """
    Run a backup into restore_snapshot_dir instead of the normal backup_dest.
    Calls backup primitives directly to redirect output without touching config.
    """
    cfg = get_config()
    snapshot_dir = Path(cfg.restore_snapshot_dir) / container.name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    from .backup_engine import (
        _backup_data_dir,
        _backup_compose,
        _backup_named_volumes,
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
        vol_records = _backup_named_volumes(container, snapshot_dir, timestamp, _log)
        for r in vol_records:
            r.is_restore_snapshot = True
        records.extend(vol_records)

    return records


# ── Restore: data directory ────────────────────────────────────────────────────

def _restore_data(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    backup_path = _resolve_backup_path(container.name, backup_id)
    if not backup_path:
        _log(f"Backup file not found: {backup_id}", "error")
        return False

    if not container.data_dir:
        _log("No data directory known for this container — cannot restore data", "error")
        return False

    data_dir = Path(container.data_dir)
    if data_dir.exists():
        _log(f"Clearing data directory: {data_dir}")
        for item in data_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    else:
        _log(f"Data directory missing, creating: {data_dir}", "warning")
        data_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Extracting {backup_id} -> {data_dir}")
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if len(parts) <= 1:
                    # Top-level directory entry itself — nothing to extract.
                    # A bare file at archive root keeps its name as-is.
                    if member.isdir():
                        continue
                else:
                    # Strip the leading directory (the data dir's name at
                    # backup time) so contents extract INTO data_dir even
                    # if the directory has been renamed since the backup.
                    member.name = str(Path(*parts[1:]))
                tar.extract(member, path=data_dir, **_EXTRACT_KWARGS)
        _log("Data extraction complete")
        return True
    except Exception as e:
        _log(f"Extraction failed: {e}", "error")
        return False


# ── Restore: compose files ─────────────────────────────────────────────────────

def _restore_compose(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    backup_path = _resolve_backup_path(container.name, backup_id)
    if not backup_path:
        _log(f"Compose backup not found: {backup_id}", "error")
        return False

    if not container.compose or not container.compose.working_dir:
        _log("No compose working directory known — cannot restore compose", "error")
        return False

    working_dir = Path(container.compose.working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)

    _log(f"Extracting compose archive to {working_dir}")
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(path=working_dir, **_EXTRACT_KWARGS)
        _log("Compose extraction complete")
        return True
    except Exception as e:
        _log(f"Compose extraction failed: {e}", "error")
        return False


# ── Restore: internal volumes ──────────────────────────────────────────────────

def _restore_internal(
    container: ContainerInfo,
    backup_id: str,
    _log: LogCallback,
) -> bool:
    """
    Restore all named volumes that share the same timestamp as backup_id.

    The reference file may live in the backup dir or the snapshot dir —
    the full set is collected from whichever directory the reference
    file was found in.
    """
    backup_path = _resolve_backup_path(container.name, backup_id)
    if not backup_path:
        _log(f"Internal backup not found: {backup_id}", "error")
        return False

    backup_dir = backup_path.parent

    ref_record = BackupRecord.from_path(str(backup_path))
    if not ref_record:
        _log(f"Cannot parse backup ID: {backup_id}", "error")
        return False

    ts_str = ref_record.timestamp.strftime("%Y-%m-%d_%H-%M-%S")
    _log(f"Restoring internal snapshot: {ts_str}")

    # Collect all internal files in the same directory sharing this timestamp
    vol_records: List[BackupRecord] = []
    legacy_records: List[BackupRecord] = []

    for f in backup_dir.iterdir():
        if not f.is_file():
            continue
        r = BackupRecord.from_path(str(f))
        if not r or r.backup_type != BackupType.INTERNAL:
            continue
        if r.timestamp.strftime("%Y-%m-%d_%H-%M-%S") != ts_str:
            continue
        if r.volume_name:
            vol_records.append(r)
        else:
            legacy_records.append(r)

    if not vol_records and not legacy_records:
        _log(f"No internal backup files found for timestamp {ts_str}", "error")
        return False

    _log(
        f"Found {len(vol_records)} volume archive(s)"
        + (f" + {len(legacy_records)} legacy image(s)" if legacy_records else "")
    )

    restored = 0

    # ── Named volumes via busybox ──────────────────────────────────────────────
    for vr in vol_records:
        _log(f"Restoring volume '{vr.volume_name}' from {vr.filename}")
        ok = dc.restore_named_volume(
            vr.volume_name,
            str(backup_dir),
            vr.filename,
        )
        if ok:
            restored += 1
            _log(f"  ✓ Volume '{vr.volume_name}' restored")
        else:
            _log(f"  ✗ Failed to restore volume '{vr.volume_name}'", "error")

    # ── Legacy docker-save images ──────────────────────────────────────────────
    for lr in legacy_records:
        _log(f"Loading legacy docker image: {lr.filename}")
        image_id = dc.load_image(str(backup_dir / lr.filename))
        if image_id:
            _log(f"  Legacy image loaded: {image_id[:12]}")
            _log(
                "  NOTE: recreate the container from this image to apply the layer restore",
                "warning",
            )
            restored += 1
        else:
            _log(f"  Failed to load legacy image: {lr.filename}", "error")

    return restored > 0


# ── Snapshot cleanup ───────────────────────────────────────────────────────────

def _purge_snapshot_records(records: List[BackupRecord], _log: LogCallback) -> None:
    for r in records:
        try:
            if os.path.exists(r.filepath):
                os.remove(r.filepath)
                _log(f"Purged snapshot: {r.filename}")
        except Exception as e:
            _log(f"Could not purge {r.filename}: {e}", "warning")


def list_restore_snapshots(container_name: str) -> List[BackupRecord]:
    """List safety snapshots for a container, newest first."""
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
