"""
Docker SDK wrapper. All docker interaction goes through this module.
Keeps the SDK import isolated so tests can mock it cleanly.
"""

from __future__ import annotations
import docker
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

from .models import ContainerInfo, ContainerStatus, MountInfo
from .config import get_config

log = logging.getLogger(__name__)

# Module-level client — initialized on first use
_client: Optional[docker.DockerClient] = None


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


# ── Container list ─────────────────────────────────────────────────────────────

def list_containers(all_containers: bool = True) -> List[ContainerInfo]:
    """Return all containers as ContainerInfo objects."""
    cfg = get_config()
    client = get_client()
    results = []

    for c in client.containers.list(all=all_containers):
        info = _parse_container(c, cfg.self_container_name, cfg.base_data_dir)
        results.append(info)

    return sorted(results, key=lambda x: x.name)


def get_container(name: str) -> Optional[ContainerInfo]:
    """Get a single container by name or ID."""
    cfg = get_config()
    client = get_client()
    try:
        c = client.containers.get(name)
        return _parse_container(c, cfg.self_container_name, cfg.base_data_dir)
    except docker.errors.NotFound:
        return None


def stop_container(name: str) -> bool:
    client = get_client()
    try:
        c = client.containers.get(name)
        if c.status == "running":
            c.stop(timeout=30)
            log.info(f"Stopped container: {name}")
        return True
    except docker.errors.NotFound:
        log.warning(f"Container not found when stopping: {name}")
        return False
    except Exception as e:
        log.error(f"Error stopping {name}: {e}")
        return False


def start_container(name: str) -> bool:
    client = get_client()
    try:
        c = client.containers.get(name)
        c.start()
        log.info(f"Started container: {name}")
        return True
    except docker.errors.NotFound:
        log.warning(f"Container not found when starting: {name}")
        return False
    except Exception as e:
        log.error(f"Error starting {name}: {e}")
        return False


# ── Internal data (named volumes / container layer) ────────────────────────────

def commit_container(name: str, tag: str) -> Optional[str]:
    """
    Commit a container to a new image. Returns the image ID or None on failure.
    Used to capture internal container state before save.
    """
    client = get_client()
    try:
        c = client.containers.get(name)
        image = c.commit(repository="dbm-internal-backup", tag=tag)
        log.info(f"Committed container {name} -> image {image.id[:12]}")
        return image.id
    except Exception as e:
        log.error(f"Error committing {name}: {e}")
        return None


def save_image(image_id: str, dest_path: str) -> bool:
    """Save a docker image to a tar file at dest_path."""
    client = get_client()
    try:
        image = client.images.get(image_id)
        with open(dest_path, "wb") as f:
            for chunk in image.save(named=False):
                f.write(chunk)
        log.info(f"Saved image {image_id[:12]} to {dest_path}")
        return True
    except Exception as e:
        log.error(f"Error saving image {image_id}: {e}")
        return False


def remove_image(image_id: str) -> None:
    """Remove a temporary backup image."""
    client = get_client()
    try:
        client.images.remove(image_id, force=True)
        log.info(f"Removed temporary image {image_id[:12]}")
    except Exception as e:
        log.warning(f"Could not remove image {image_id}: {e}")


def load_image(tar_path: str) -> Optional[str]:
    """Load an image from a tar file. Returns image ID or None."""
    client = get_client()
    try:
        with open(tar_path, "rb") as f:
            images = client.images.load(f)
        if images:
            image_id = images[0].id
            log.info(f"Loaded image from {tar_path}: {image_id[:12]}")
            return image_id
        return None
    except Exception as e:
        log.error(f"Error loading image from {tar_path}: {e}")
        return None


def backup_named_volume(volume_name: str, dest_dir: str, filename: str) -> bool:
    """
    Backup a named Docker volume using a busybox helper container.
    Volume is mounted read-only at /vol; dest_dir is mounted at /backup.
    Output written to /backup/{filename}.
    """
    client = get_client()
    try:
        client.containers.run(
            image="busybox",
            command=f"tar czf /backup/{filename} -C /vol .",
            volumes={
                volume_name: {"bind": "/vol", "mode": "ro"},
                dest_dir:    {"bind": "/backup", "mode": "rw"},
            },
            remove=True,
        )
        log.info(f"Backed up volume {volume_name} -> {dest_dir}/{filename}")
        return True
    except Exception as e:
        log.error(f"Error backing up volume {volume_name}: {e}")
        return False


def restore_named_volume(volume_name: str, backup_dir: str, filename: str) -> bool:
    """
    Restore a named volume from a tar produced by backup_named_volume.
    backup_dir is mounted at /backup; volume mounted rw at /vol.
    """
    client = get_client()
    try:
        client.containers.run(
            image="busybox",
            command=f"tar xzf /backup/{filename} -C /vol",
            volumes={
                volume_name: {"bind": "/vol", "mode": "rw"},
                backup_dir:  {"bind": "/backup", "mode": "ro"},
            },
            remove=True,
        )
        log.info(f"Restored volume {volume_name} from {backup_dir}/{filename}")
        return True
    except Exception as e:
        log.error(f"Error restoring volume {volume_name}: {e}")
        return False


def list_named_volumes(container_name: str) -> List[Dict[str, Any]]:
    """
    Return named volume mounts for a container.
    Each entry: {name, destination, read_write}
    """
    client = get_client()
    try:
        c = client.containers.get(container_name)
        return [
            {
                "name": m["Name"],
                "destination": m["Destination"],
                "read_write": m.get("RW", True),
            }
            for m in c.attrs.get("Mounts", [])
            if m.get("Type") == "volume" and m.get("Name")
        ]
    except Exception as e:
        log.error(f"Error listing volumes for {container_name}: {e}")
        return []


# ── Raw inspect ────────────────────────────────────────────────────────────────

def inspect_raw(name: str) -> Optional[Dict[str, Any]]:
    """Return raw docker inspect output for a container."""
    client = get_client()
    try:
        c = client.containers.get(name)
        return c.attrs
    except docker.errors.NotFound:
        return None


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_container(
    c: Any,
    self_name: Optional[str],
    base_data_dir: str,
) -> ContainerInfo:
    """Convert a docker SDK Container object to ContainerInfo."""
    import os

    status_map = {
        "running": ContainerStatus.RUNNING,
        "stopped": ContainerStatus.STOPPED,
        "paused": ContainerStatus.PAUSED,
        "restarting": ContainerStatus.RESTARTING,
        "exited": ContainerStatus.EXITED,
    }

    name = c.name
    status = status_map.get(c.status, ContainerStatus.UNKNOWN)

    # Parse mounts
    mounts = []
    has_external = False
    has_internal_vol = False

    for m in c.attrs.get("Mounts", []):
        mi = MountInfo(
            mount_type=m.get("Type", "unknown"),
            source=m.get("Source", ""),
            destination=m.get("Destination", ""),
            read_write=m.get("RW", True),
            name=m.get("Name"),
        )
        mounts.append(mi)
        if mi.mount_type == "bind":
            has_external = True
        elif mi.mount_type == "volume":
            has_internal_vol = True

    # Try to match a data directory under base_data_dir
    data_dir = _find_data_dir(name, base_data_dir)

    # Parse created timestamp
    created_str = c.attrs.get("Created", "")
    created = None
    if created_str:
        try:
            created = datetime.fromisoformat(created_str.split(".")[0].replace("Z", ""))
        except Exception:
            pass

    return ContainerInfo(
        id=c.id,
        short_id=c.short_id,
        name=name,
        status=status,
        image=c.image.tags[0] if c.image.tags else c.image.short_id,
        created=created,
        data_dir=data_dir,
        mounts=mounts,
        has_external_mounts=has_external,
        has_internal_volumes=has_internal_vol,
        is_self=(name == self_name) if self_name else False,
    )


def _find_data_dir(container_name: str, base_data_dir: str) -> Optional[str]:
    """
    Look for a directory under base_data_dir whose name matches
    the container name (case-insensitive). Returns full path or None.
    """
    import os
    if not base_data_dir or not os.path.isdir(base_data_dir):
        return None
    for entry in os.scandir(base_data_dir):
        if entry.is_dir() and entry.name.lower() == container_name.lower():
            return entry.path
    return None
