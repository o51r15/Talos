"""
Backup engine. Orchestrates stop → backup → restart for a single container.

Backup types:
  DATA     — tar.gz of the host-mapped data directory
  COMPOSE  — tar.gz of the discovered compose file(s)
  INTERNAL — docker commit + docker save for named volumes / container layer
"""

from __future__ import annotations
import os
import tarfile
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Callable

from .models import BackupOptions, BackupRecord, BackupType, ContainerInfo
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
        if options.backup_data and container.data_dir:
            record = _backup_data_dir(container, dest_dir, timestamp, _log)
            if record:
                records.append(record)
        elif options.backup_data:
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

        # ── INTERNAL backup ───────────────────────────────────────────────────
        if options.backup_internal and container.has_internal_volumes:
            record = _backup_internal(container, dest_dir, timestamp, _log)
            if record:
                records.append(record)

    finally:
        # ── Restart container ─────────────────────────────────────────────────
        if was_running:
            _log(f"Starting container: {container.name}")
            dc.start_container(container.name)

    _log(f"Backup complete for {container.name}: {len(records)} archive(s) created")
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
        with tarfile.open(dest_path, "w:gz") as tar:
            for cf in config_files:
                if os.path.isfile(cf):
                    # Store relative to working_dir if possible
                    if working_dir and cf.startswith(working_dir):
                        arcname = os.path.relpath(cf, working_dir)
                    else:
                        arcname = os.path.basename(cf)
                    tar.add(cf, arcname=arcname)
            # Also include .env if present in working_dir
            if working_dir:
                env_file = os.path.join(working_dir, ".env")
                if os.path.isfile(env_file):
                    tar.add(env_file, arcname=".env")
                    _log("Included .env file in compose backup")

        size = dest_path.stat().st_size
        _log(f"Compose backup saved: {filename} ({_human_size(size)})")
        return BackupRecord.from_path(str(dest_path))
    except Exception as e:
        _log(f"Compose backup failed: {e}", "error")
        return None


# ── Internal data backup ───────────────────────────────────────────────────────

def _backup_internal(
    container: ContainerInfo,
    dest_dir: Path,
    timestamp: str,
    _log: LogCallback,
) -> Optional[BackupRecord]:
    """
    Capture internal container state via docker commit + docker save.
    This covers named volumes and container-layer data not reflected on the host.
    """
    filename = f"{container.name}_internal_{timestamp}.tar"
    dest_path = dest_dir / filename
    tag = f"{container.name}-{timestamp}".replace(":", "-")

    _log(f"Committing container {container.name} to temporary image")
    image_id = dc.commit_container(container.name, tag)
    if not image_id:
        _log("docker commit failed — skipping internal backup", "error")
        return None

    try:
        _log(f"Saving image to {filename}")
        if not dc.save_image(image_id, str(dest_path)):
            _log("docker save failed", "error")
            return None
        size = dest_path.stat().st_size
        _log(f"Internal backup saved: {filename} ({_human_size(size)})")
        return BackupRecord.from_path(str(dest_path))
    finally:
        dc.remove_image(image_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
