"""
Shared data models for the backup manager.
Pydantic models are used throughout — API responses, internal state, and CLI output.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum


# ── Enums ─────────────────────────────────────────────────────────────────────

class ContainerStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    PAUSED = "paused"
    RESTARTING = "restarting"
    EXITED = "exited"
    UNKNOWN = "unknown"


class BackupType(str, Enum):
    DATA = "data"
    COMPOSE = "compose"
    INTERNAL = "internal"   # docker named volumes (per-volume tars) or container layer


class JobType(str, Enum):
    BACKUP = "backup"
    RESTORE = "restore"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Compose ───────────────────────────────────────────────────────────────────

class ComposeInfo(BaseModel):
    project_name: Optional[str] = None
    config_files: List[str] = Field(default_factory=list)
    working_dir: Optional[str] = None
    shared_containers: List[str] = Field(default_factory=list)
    discovered: bool = False
    discovery_method: str = "none"    # "labels" | "scan" | "none"


# ── Mount ─────────────────────────────────────────────────────────────────────

class MountInfo(BaseModel):
    mount_type: str          # "bind" | "volume" | "tmpfs"
    source: str
    destination: str
    read_write: bool = True
    name: Optional[str] = None


# ── Data source ───────────────────────────────────────────────────────────────

class DataSource(BaseModel):
    """
    A host path (directory OR single file) that holds container data.

    Sources are discovered in priority order:
      "bind"      — a bind mount from docker inspect (authoritative; the
                    container literally uses this host path). destination is
                    the in-container path it maps to.
      "name"      — a folder under base_data_dir whose name matches the
                    container (convenience fallback for the simple case).
      "manual"    — explicitly configured in config.yaml extra_data_sources.

    kind is "dir" or "file" (bind-mounted single files are common: configs,
    certs). A source whose host path is not currently visible is still kept —
    backup warns and skips it rather than silently forgetting it exists.
    """
    host_path: str
    destination: Optional[str] = None   # in-container path (bind mounts only)
    method: str = "bind"                 # "bind" | "name" | "manual"
    kind: str = "dir"                    # "dir" | "file"
    read_write: bool = True


# ── Container ─────────────────────────────────────────────────────────────────

class ContainerInfo(BaseModel):
    id: str
    short_id: str
    name: str
    status: ContainerStatus
    image: str
    created: Optional[datetime] = None
    data_dir: Optional[str] = None          # legacy single-path (first data source)
    data_sources: List[DataSource] = Field(default_factory=list)
    mounts: List[MountInfo] = Field(default_factory=list)
    has_external_mounts: bool = False
    has_internal_volumes: bool = False
    compose: Optional[ComposeInfo] = None
    is_self: bool = False
    last_backup: Optional[datetime] = None
    backup_count: int = 0


# ── Backup Record ─────────────────────────────────────────────────────────────

class BackupRecord(BaseModel):
    id: str
    container_name: str
    backup_type: BackupType
    timestamp: datetime
    filename: str
    filepath: str
    size_bytes: int = 0
    size_human: str = ""
    volume_name: Optional[str] = None      # populated for per-volume internal backups
    compose_project: Optional[str] = None
    is_restore_snapshot: bool = False

    @classmethod
    def from_path(cls, filepath: str) -> Optional["BackupRecord"]:
        """
        Parse a BackupRecord from a filename. Returns None if unparseable.

        Supported filename patterns:
          {container}_data_{ts}.tar.gz
          {container}_compose_{ts}.tar.gz
          {container}_internal_{ts}.tar.gz         — future: docker-layer backup
          {container}_internal_vol-{volname}_{ts}.tar.gz  — named volume backup
        """
        import os
        import re

        filename = os.path.basename(filepath)
        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

        # Match type segment: data | compose | internal | internal_vol-{anything-without-underscore-digit-pairs}
        # Timestamp is always YYYY-MM-DD_HH-MM-SS at the end before extension
        pattern = (
            r"^(.+?)"                               # container name (non-greedy)
            r"_(data|compose|internal(?:_vol-[^_]+)?)"  # backup type + optional vol suffix
            r"_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})"  # timestamp
            r"\.tar(?:\.gz)?$"
        )
        match = re.match(pattern, filename)
        if not match:
            return None

        container_name, raw_type, ts_str = match.groups()

        # Extract volume name if present
        volume_name: Optional[str] = None
        if raw_type.startswith("internal_vol-"):
            volume_name = raw_type[len("internal_vol-"):]
        enum_type = "internal" if raw_type.startswith("internal") else raw_type

        try:
            timestamp = datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None

        return cls(
            id=filename,
            container_name=container_name,
            backup_type=BackupType(enum_type),
            timestamp=timestamp,
            filename=filename,
            filepath=filepath,
            size_bytes=size,
            size_human=_human_size(size),
            volume_name=volume_name,
            is_restore_snapshot="restore_snapshot" in filepath,
        )


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ── Operation Options ─────────────────────────────────────────────────────────

class BackupOptions(BaseModel):
    backup_data: bool = True
    backup_compose: bool = True
    backup_internal: bool = False
    include_compose_siblings: bool = False


class RestoreOptions(BaseModel):
    restore_data: bool = True
    restore_compose: bool = True
    restore_internal: bool = False
    backup_id_data: Optional[str] = None
    backup_id_compose: Optional[str] = None
    backup_id_internal: Optional[str] = None


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobLog(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: str = "info"    # "info" | "warning" | "error"
    message: str


class Job(BaseModel):
    id: str
    job_type: JobType
    container_name: str
    status: JobStatus = JobStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    options: dict = Field(default_factory=dict)
    log: List[JobLog] = Field(default_factory=list)
    error: Optional[str] = None

    def add_log(self, message: str, level: str = "info") -> None:
        self.log.append(JobLog(timestamp=datetime.now(timezone.utc), level=level, message=message))
