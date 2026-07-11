"""
Dry-run preview. Reports exactly what a backup WOULD do for a container —
which data sources, compose files, and volumes would be archived, where they
were discovered from, and where archives would be written — WITHOUT stopping
any container or writing any file.

Used by:
  - CLI:  python main.py backup --dry-run
  - CLI:  python main.py inspect <container>   (detailed discovery report)
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, Any

from .models import ContainerInfo, BackupOptions
from .config import get_config


def _path_stats(path: str) -> Dict[str, Any]:
    """Count files and total bytes under a path (dir or single file)."""
    if not path:
        return {"exists": False, "files": 0, "bytes": 0}
    if os.path.isfile(path):
        try:
            return {"exists": True, "files": 1, "bytes": os.path.getsize(path)}
        except OSError:
            return {"exists": True, "files": 1, "bytes": 0}
    if not os.path.isdir(path):
        return {"exists": False, "files": 0, "bytes": 0}
    total_bytes = 0
    file_count = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total_bytes += os.path.getsize(os.path.join(root, fn))
                file_count += 1
            except OSError:
                pass
    return {"exists": True, "files": file_count, "bytes": total_bytes}


def _human(size_bytes: int) -> str:
    b = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def preview_backup(container: ContainerInfo, options: BackupOptions) -> Dict[str, Any]:
    """
    Build a structured preview of what a backup would do.
    Pure inspection — touches the filesystem read-only, never Docker.
    """
    cfg = get_config()
    dest_dir = Path(cfg.backup_dest) / container.name

    plan: Dict[str, Any] = {
        "container": container.name,
        "status": container.status.value,
        "is_self": container.is_self,
        "will_stop": container.status.value == "running" and not container.is_self,
        "dest_dir": str(dest_dir),
        "data": {"enabled": options.backup_data, "sources": [], "total_bytes": 0},
        "compose": {"enabled": options.backup_compose, "files": [], "discovered": False,
                    "method": "none", "working_dir": None},
        "internal": {"enabled": options.backup_internal, "volumes": []},
        "siblings": [],
        "warnings": [],
    }

    # ── Data sources ───────────────────────────────────────────────────────────
    if options.backup_data:
        if not container.data_sources:
            plan["warnings"].append(
                "No host data directories found (no usable bind mounts, no name match). "
                "Data backup would be SKIPPED. If this container keeps data in named "
                "volumes, enable internal backup instead."
            )
        for s in container.data_sources:
            stats = _path_stats(s.host_path)
            plan["data"]["sources"].append({
                "host_path": s.host_path,
                "destination": s.destination,
                "method": s.method,
                "kind": s.kind,
                "exists": stats["exists"],
                "files": stats["files"],
                "bytes": stats["bytes"],
                "human": _human(stats["bytes"]),
            })
            plan["data"]["total_bytes"] += stats["bytes"]
            if not stats["exists"]:
                plan["warnings"].append(
                    f"Data source path not visible from this process: {s.host_path}. "
                    f"It would be SKIPPED. If running as a container, mount this "
                    f"path into the manager at the same location."
                )
        plan["data"]["total_human"] = _human(plan["data"]["total_bytes"])

    # ── Compose ────────────────────────────────────────────────────────────────
    if options.backup_compose:
        if container.compose:
            plan["compose"]["discovered"] = container.compose.discovered
            plan["compose"]["method"] = container.compose.discovery_method
            plan["compose"]["working_dir"] = container.compose.working_dir
            for cf in container.compose.config_files:
                plan["compose"]["files"].append({
                    "path": cf,
                    "exists": os.path.isfile(cf),
                })
            if not container.compose.config_files:
                plan["warnings"].append(
                    "Compose enabled but no compose files were discovered — "
                    "compose backup would be SKIPPED."
                )
            else:
                for cf in container.compose.config_files:
                    if not os.path.isfile(cf):
                        plan["warnings"].append(f"Compose file not found on disk: {cf}")
        else:
            plan["warnings"].append("Compose enabled but no compose info discovered.")

    # ── Internal volumes ───────────────────────────────────────────────────────
    if options.backup_internal:
        if not container.has_internal_volumes:
            plan["warnings"].append(
                "Internal backup enabled but no named volumes detected — would be SKIPPED."
            )
        for m in container.mounts:
            if m.mount_type == "volume" and m.name:
                plan["internal"]["volumes"].append({
                    "name": m.name,
                    "destination": m.destination,
                })

    # ── Compose siblings ───────────────────────────────────────────────────────
    if options.include_compose_siblings and container.compose:
        plan["siblings"] = list(container.compose.shared_containers)

    return plan
