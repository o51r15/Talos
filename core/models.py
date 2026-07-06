"""
Shared data models for the backup manager.
Pydantic models are used throughout — API responses, internal state, and CLI output.
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
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
    DATA = "data"           # host-mounted data directory tar
    COMPOSE = "compose"     # compose file(s) tar
    INTERNAL = "internal"   # docker commit + save for named volumes / container layer


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
    discovered: bool = False          # True if labels were found
    discovery_method: str = "none"    # "labels" | "scan" | "none"


# ── Mount ─────────────────────────────────────────────────────────────────────

class MountInfo(BaseModel):
    mount_type: str          # "bind" | "volume" | "tmpfs"
    source: str              # host path (bind) or volume name (volume)
    destination: str         # container path
    read_write: bool = True
    name: Optional[str] = None   # volume name if type == "volume"


# ── Container ─────────────────────────────────────────────────────────────────

class ContainerInfo(BaseModel):
    id: str
    short_id: str
    name: str
    status: ContainerStatus
    image: str
    created: Optional[datetime] = None
    data_dir: Optional[str] = None       # matched host data directory
    mounts: List[MountInfo] = Field(default_factory=list)
    has_external_mounts: bool = False    # has bind mounts to host
    has_internal_volumes: bool = False   # has named docker volumes
    compose: Optional[ComposeInfo] = None
    is_self: bool = False                # is this the backup manager container?
    last_backup: Optional[datetime] = None
    backup_count: int = 0


# ── Backup Record ─────────────────────────────────────────────────────────────

class BackupRecord(BaseModel):
    id: str                              # derived from filename
    container_name: str
    backup_type: BackupType
    timestamp: datetime
    filename: str
    filepath: str
    size_bytes: int = 0
    size_human: str = ""
    compose_project: Optional[str] = None
    is_restore_snapshot: bool = False

    @classmethod
    def from_path(cls, filepath: str) -> Optional["BackupRecord"]:
        """Parse a backup record from a filename. Returns None if unparseable."""
        import os
        import re
        from datetime import datetime

        filename = os.path.basename(filepath)
        size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

        # Pattern: {name}_{type}_{YYYY-MM-DD_HH-MM-SS}.tar.gz  or  .tar
        pattern = r'^(.+?)_(data|compose|internal)_(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.tar(?:\.gz)?$'
        match = re.match(pattern, filename)
        if not match:
            return None

        container_name, btype, ts_str = match.groups()
        try:
            timestamp = datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None

        return cls(
            id=filename,
            container_name=container_name,
            backup_type=BackupType(btype),
            timestamp=timestamp,
            filename=filename,
            filepath=filepath,
            size_bytes=size,
            size_human=_human_size(size),
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
    backup_internal: bool = False        # off by default — only when needed
    include_compose_siblings: bool = False


class RestoreOptions(BaseModel):
    restore_data: bool = True
    restore_compose: bool = True
    restore_internal: bool = False
    backup_id_data: Optional[str] = None      # filename of selected data backup
    backup_id_compose: Optional[str] = None   # filename of selected compose backup
    backup_id_internal: Optional[str] = None


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobLog(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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
        self.log.append(JobLog(timestamp=datetime.utcnow(), level=level, message=message))
