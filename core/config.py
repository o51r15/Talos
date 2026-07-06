"""
Configuration loader. Reads config.yaml and provides a typed AppConfig object.
Config is loaded once at startup and cached. Call reload_config() to refresh.
"""

from __future__ import annotations
import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List

# Config file is looked up in order:
#   1. CONFIG_PATH env var
#   2. /app/config.yaml  (container default)
#   3. ./config.yaml     (local dev)
DEFAULT_PATHS = [
    os.environ.get("CONFIG_PATH", ""),
    "/app/config.yaml",
    "config.yaml",
]


# ── Sub-models ────────────────────────────────────────────────────────────────

class RetentionConfig(BaseModel):
    keep_last: int = 7
    max_age_days: int = 30


class AuthConfig(BaseModel):
    enabled: bool = False
    secret_key: str = "changeme"
    username: str = "admin"
    password_hash: str = ""


# ── Main config ───────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    backup_dest: str = "/backups"
    restore_snapshot_dir: str = "/backups/restore_snapshot"
    base_data_dir: str = "/docker"
    compose_scan_paths: List[str] = Field(default_factory=lambda: ["/docker", "/opt/docker", "/home"])
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    purge_snapshot_on_success: bool = False
    self_container_name: Optional[str] = None
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    auth: AuthConfig = Field(default_factory=AuthConfig)


# ── Singleton ─────────────────────────────────────────────────────────────────

_config: Optional[AppConfig] = None
_config_path: Optional[str] = None


def load_config(path: Optional[str] = None) -> AppConfig:
    """Load config from disk. Tries DEFAULT_PATHS if path is not given."""
    global _config, _config_path

    if path:
        candidates = [path]
    else:
        candidates = [p for p in DEFAULT_PATHS if p]

    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            with open(p, "r") as f:
                raw = yaml.safe_load(f) or {}
            _config = AppConfig(**raw)
            _config_path = str(p)
            return _config

    # No config file found — use defaults and warn
    print("[config] No config.yaml found — using built-in defaults")
    _config = AppConfig()
    return _config


def get_config() -> AppConfig:
    """Return cached config, loading it on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """Force reload from disk."""
    global _config
    _config = None
    return load_config(_config_path)


def get_config_path() -> Optional[str]:
    return _config_path
