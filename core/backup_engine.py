"""
Backup engine. Orchestrates stop → backup → restart for a single container.

Backup types:
  DATA     — tar.gz of the host-mapped data directory
  COMPOSE  — tar.gz of the discovered compose file(s) and .env
  INTERNAL — per named-volume tar.gz archives via busybox helper

Named volumes are backed up individually:
  {container}_internal_vol-{volname}_{timestamp}.tar.gz

Container-layer-only changes (no named volumes) are noted but not captured
in this release — most real workloads use volumes for persistent data.

Compose siblings: when options.include_compose_siblings is True, after backing
up the primary container the engine fetches each sibling by name, enriches it
with compose data, and recursively calls run_backup (with siblings=False to
prevent loops). Sibling records are appended to the returned list.
"""

from __future__ import annotations
import os
import tarfile
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Callable

from .models import BackupOptions, BackupRecord, ContainerInfo
from .config import get_config
from . import docker_client as dc

log = logging.getLogger(__name__)

LogCallback = Callable[[str, str], None]   # (message, level)


# ── Public entry point ─────────────────────────────────────────────────────────

def run_backup(
    container: ContainerInfo,
    options: BackupOptions,
    log_cb: Optional[LogCallback] = None,
) -> List[BackupRecord]:
    """
    Back up one container according to options.
    Returns a list of BackupRecord objects for each file produced.
    Raises on unrecoverable errors; caller (job runner) handles exceptions.
    """
    def _log(msg: str, level: str = "info") -> None:
        log.log(getattr(logging, level.upper(), logging.INFO), msg)
        if log_cb:
            log_cb(msg, level)

    cfg = get_config()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    records: List[BackupRecord] = []

    dest_dir = Path(cfg.backup_dest) / container.name
    dest_dir.mkdir(parents=True, exist_ok=True)

    was_running = container.status.value == "running"

    # ── Stop container ─────────────────────────────────────────────────────────
    if was_running:
        _log(f"Stopping container: {container.name}")
        if not dc.stop_container(container.name):
            raise RuntimeError(f"Failed to stop container {container.name}")

    try:
        # ── DATA backup ───────────────────────────────────────────────────────
        if options.backup_data:
            if container.data_dir:
                record = _backup_data_dir(container, dest_dir, timestamp, _log)
                if record:
                    records.append(record)
            else:
                _log(
                    f"No data directory found for {container.name} — skipping data backup",
                    "warning",
                )

        # ── COMPOSE backup ────────────────────────────────────────────────────
        if options.backup_compose:
            if container.compose and container.compose.config_files:
                record = _backup_compose(container, dest_dir, timestamp, _log)
                if record:
                    records.append(record)
            else:
                _log(
                    f"No compose files discovered for {container.name} — skipping compose backup",
                    "warning",
                )

        # ── INTERNAL (named volumes) backup ───────────────────────────────────
        if options.backup_internal:
            if container.has_internal_volumes:
                vol_records = _backup_named_volumes(container, dest_dir, timestamp, _log)
                records.extend(vol_records)
                if not vol_records:
                    _log(
                        f"No named volumes could be backed up for {container.name}",
                        "warning",
                    )
            else:
                _log(
                    f"No named volumes detected for {container.name} — skipping internal backup",
                    "warning",
                )

    finally:
        # ── Restart container ─────────────────────────────────────────────────
        if was_running:
            _log(f"Starting container: {container.name}")
            dc.start_container(container.name)

    _log(f"Backup complete for {container.name}: {len(records)} archive(s) created")

    # ── Compose siblings ──────────────────────────────────────────────────────
    # Done AFTER the primary container is restarted so siblings run independently.
    if options.include_compose_siblings and container.compose and container.compose.shared_containers:
        sibling_names = container.compose.shared_containers
        _log(f"Backing up {len(sibling_names)} compose sibling(s): {', '.join(sibling_names)}")
        sibling_opts = options.model_copy(update={"include_compose_siblings": False})
        records.extend(_backup_siblings(sibling_names, sibling_opts, _log))

    return records


# ── Data directory backup ──────────────────────────────────────────────────────

def _backup_data_dir(
    container: ContainerInfo,
    dest_dir: Path,
    timestamp: str,
    _log: LogCallback,
) -> Optional[BackupRecord]:
    filename = f"{container.name}_data_{timestamp}.tar.gz"
    dest_path = dest_dir / filename

    _log(f"Compressing data directory: {container.data_dir}")
    try:
        data_path = Path(container.data_dir)
        with tarfile.open(dest_path, "w:gz") as tar:
            tar.add(data_path, arcname=data_path.name)
        size = dest_path.stat().st_size
        _log(f"Data backup saved: {filename} ({_human_size(size)})")
        return BackupRecord.from_path(str(dest_path))
    except Exception as e:
        _log(f"Data backup failed: {e}", "error")
        return None


# ── Compose backup ─────────────────────────────────────────────────────────────

def _backup_compose(
    container: ContainerInfo,
    dest_dir: Path,
    timestamp: str,
    _log: LogCallback,
) -> Optional[BackupRecord]:
    filename = f"{container.name}_compose_{timestamp}.tar.gz"
    dest_path = dest_dir / filename
    config_files = container.compose.config_files
    working_dir = container.compose.working_dir

    _log(f"Archiving compose file(s): {config_files}")
    try:
        # Path-safe prefix: '/docker/app' must not match '/docker/app2/compose.yml'
        wd_prefix = working_dir.rstrip(os.sep) + os.sep if working_dir else None
        with tarfile.open(dest_path, "w:gz") as tar:
            for cf in config_files:
                if os.path.isfile(cf):
                    if wd_prefix and cf.startswith(wd_prefix):
                        arcname = os.path.relpath(cf, working_dir)
                    else:
                        arcname = os.path.basename(cf)
                    tar.add(cf, arcname=arcname)

            # Include .env if present alongside the compose file
            if working_dir:
                env_file = os.path.join(working_dir, ".env")
                if os.path.isfile(env_file):
                    tar.add(env_file, arcname=".env")
                    _log("Included .env in compose backup")

        size = dest_path.stat().st_size
        _log(f"Compose backup saved: {filename} ({_human_size(size)})")
        return BackupRecord.from_path(str(dest_path))
    except Exception as e:
        _log(f"Compose backup failed: {e}", "error")
        return None


# ── Named volume backup ────────────────────────────────────────────────────────

def _backup_named_volumes(
    container: ContainerInfo,
    dest_dir: Path,
    timestamp: str,
    _log: LogCallback,
) -> List[BackupRecord]:
    """
    Back up each named volume individually using a busybox helper container.
    Volume names are sanitised for use in filenames (/ and : replaced with -).
    Returns one BackupRecord per volume successfully backed up.
    """
    volumes = dc.list_named_volumes(container.name)
    if not volumes:
        _log(f"No named volumes found for {container.name}", "warning")
        return []

    records: List[BackupRecord] = []
    dest_dir_str = str(dest_dir)

    for vol in volumes:
        vol_name = vol["name"]
        safe_vol = vol_name.replace("/", "-").replace(":", "-")
        filename = f"{container.name}_internal_vol-{safe_vol}_{timestamp}.tar.gz"

        _log(f"Backing up named volume: {vol_name} -> {filename}")
        ok = dc.backup_named_volume(vol_name, dest_dir_str, filename)
        if ok:
            dest_path = dest_dir / filename
            record = BackupRecord.from_path(str(dest_path))
            if record:
                records.append(record)
                _log(f"Volume backup saved: {filename} ({record.size_human})")
        else:
            _log(f"Failed to backup volume: {vol_name}", "error")

    return records


# ── Compose siblings ───────────────────────────────────────────────────────────

def _backup_siblings(
    sibling_names: List[str],
    options: BackupOptions,
    _log: LogCallback,
) -> List[BackupRecord]:
    """
    Fetch, enrich, and backup each sibling container.
    Errors per sibling are logged but don't abort the others.
    """
    from .compose_discovery import enrich_with_compose

    all_records: List[BackupRecord] = []
    for name in sibling_names:
        sibling = dc.get_container(name)
        if not sibling:
            _log(f"Sibling not found, skipping: {name}", "warning")
            continue
        if sibling.is_self:
            _log(f"Sibling is self, skipping: {name}", "warning")
            continue
        try:
            [sibling] = enrich_with_compose([sibling])
            _log(f"── Sibling: {name}")
            records = run_backup(sibling, options, log_cb=_log)
            all_records.extend(records)
        except Exception as e:
            _log(f"Sibling backup failed for {name}: {e}", "error")

    return all_records


# ── Helpers ────────────────────────────────────────────────────────────────────

def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
