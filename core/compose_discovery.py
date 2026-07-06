"""
Compose file discovery and container grouping.

Strategy:
  1. Primary: read com.docker.compose.* labels from docker inspect.
  2. Fallback: scan paths defined in config for compose files and match by service name.
  3. If neither works, container is marked compose_unknown.

Grouping: containers that share the same compose project name are returned
as a group so the UI / CLI can prompt for all-or-single backup decisions.
"""

from __future__ import annotations
import os
import logging
from typing import Dict, List, Optional

from .models import ComposeInfo, ContainerInfo
from .docker_client import get_client
from .config import get_config

log = logging.getLogger(__name__)

# Compose label keys
_LABEL_PROJECT = "com.docker.compose.project"
_LABEL_WORKING_DIR = "com.docker.compose.project.working_dir"
_LABEL_CONFIG_FILES = "com.docker.compose.project.config_files"


# ── Public API ─────────────────────────────────────────────────────────────────

def enrich_with_compose(containers: List[ContainerInfo]) -> List[ContainerInfo]:
    """
    Attach ComposeInfo to each ContainerInfo in the list.
    Also populates shared_containers so each container knows its siblings.
    """
    # Build project → containers map
    project_members: Dict[str, List[str]] = {}

    for c in containers:
        info = _discover_for_container(c.name)
        c.compose = info
        if info.project_name:
            project_members.setdefault(info.project_name, []).append(c.name)

    # Back-fill shared_containers for each container
    for c in containers:
        if c.compose and c.compose.project_name:
            siblings = project_members.get(c.compose.project_name, [])
            c.compose.shared_containers = [s for s in siblings if s != c.name]

    return containers


def get_compose_group(project_name: str) -> List[str]:
    """Return all container names that belong to a compose project."""
    client = get_client()
    members = []
    for c in client.containers.list(all=True):
        labels = c.attrs.get("Config", {}).get("Labels") or {}
        if labels.get(_LABEL_PROJECT) == project_name:
            members.append(c.name)
    return members


# ── Discovery ──────────────────────────────────────────────────────────────────

def _discover_for_container(container_name: str) -> ComposeInfo:
    """Attempt compose discovery for a single container."""
    client = get_client()
    try:
        c = client.containers.get(container_name)
        labels = c.attrs.get("Config", {}).get("Labels") or {}
    except Exception:
        return ComposeInfo()

    # ── Method 1: labels ──────────────────────────────────────────────────────
    project = labels.get(_LABEL_PROJECT)
    if project:
        working_dir = labels.get(_LABEL_WORKING_DIR)
        config_files_raw = labels.get(_LABEL_CONFIG_FILES, "")
        config_files = [f.strip() for f in config_files_raw.split(",") if f.strip()]

        # Verify config files actually exist
        existing = [f for f in config_files if os.path.isfile(f)]

        return ComposeInfo(
            project_name=project,
            config_files=existing or config_files,  # keep even if missing, for display
            working_dir=working_dir,
            discovered=True,
            discovery_method="labels",
        )

    # ── Method 2: scan fallback ───────────────────────────────────────────────
    result = _scan_for_compose(container_name)
    if result:
        return result

    # ── Not found ─────────────────────────────────────────────────────────────
    return ComposeInfo(discovered=False, discovery_method="none")


def _scan_for_compose(container_name: str) -> Optional[ComposeInfo]:
    """
    Walk compose_scan_paths looking for compose files that reference
    the container name as a service. Stops at first match.
    """
    cfg = get_config()
    compose_filenames = {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }

    for scan_root in cfg.compose_scan_paths:
        if not os.path.isdir(scan_root):
            continue
        for dirpath, dirnames, filenames in os.walk(scan_root):
            # Skip hidden directories
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for filename in filenames:
                if filename not in compose_filenames:
                    continue
                full_path = os.path.join(dirpath, filename)
                if _compose_file_references_service(full_path, container_name):
                    project_name = os.path.basename(dirpath)
                    log.info(
                        f"Found compose for {container_name} via scan: {full_path}"
                    )
                    return ComposeInfo(
                        project_name=project_name,
                        config_files=[full_path],
                        working_dir=dirpath,
                        discovered=True,
                        discovery_method="scan",
                    )
    return None


def _compose_file_references_service(filepath: str, service_name: str) -> bool:
    """
    Quick check: does this compose file mention the service name?
    We do a simple text search first (fast), then try YAML parse for accuracy.
    """
    try:
        with open(filepath, "r", errors="ignore") as f:
            content = f.read()
        # Quick text pass
        if service_name not in content:
            return False
        # YAML parse for accuracy
        import yaml
        data = yaml.safe_load(content) or {}
        services = data.get("services", {})
        return service_name in services
    except Exception:
        return False
