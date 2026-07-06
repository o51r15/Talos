"""
Configuration loader. Reads config.yaml and provides a typed AppConfig object.
Config is loaded once at startup and cached. Call reload_config() to refresh.

Env var overrides (applied after YAML, take precedence):
  CONFIG_PATH            — path to config.yaml
  SELF_CONTAINER_NAME    — container name to skip (self-identification)
  BACKUP_DEST            — backup destination path
  RESTORE_SNAPSHOT_DIR   — restore snapshot path
  BASE_DATA_DIR          — root directory of container data folders
  WEB_HOST               — web server bind host
  WEB_PORT               — web server port (int)
  SCHEDULE_ENABLED       — "true"/"false" to override schedule.enabled
  SCHEDULE_CRON          — cron expression to override schedule.cron
"""

from __future__ import annotations
import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List

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


class ScheduleConfig(BaseModel):
    enabled: bool = False
    cron: str = "0 2 * * *"       # default: 2 am daily
    backup_data: bool = True
    backup_compose: bool = True
    backup_internal: bool = False
    skip_stopped: bool = True      # don't backup stopped containers in scheduled runs


# ── Main config ───────────────────────────────────────────────────────────────

class AppConfig(BaseModel):
    backup_dest: str = "/backups"
    restore_snapshot_dir: str = "/backups/restore_snapshot"
    base_data_dir: str = "/docker"
    compose_scan_paths: List[str] = Field(
        default_factory=lambda: ["/docker", "/opt/docker", "/home"]
    )
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    purge_snapshot_on_success: bool = False
    self_container_name: Optional[str] = None
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    auth: AuthConfig = Field(default_factory=AuthConfig)


# ── Env var overrides ─────────────────────────────────────────────────────────

def _apply_env_overrides(cfg: AppConfig) -> AppConfig:
    """
    Apply environment variable overrides on top of whatever was loaded from YAML.
    String values are stripped; empty strings are ignored (not treated as override).
    """
    def _str(key: str) -> Optional[str]:
        val = os.environ.get(key, "").strip()
        return val if val else None

    def _int(key: str) -> Optional[int]:
        val = _str(key)
        try:
            return int(val) if val is not None else None
        except ValueError:
            return None

    def _bool(key: str) -> Optional[bool]:
        val = _str(key)
        if val is None:
            return None
        return val.lower() in ("1", "true", "yes", "on")

    overrides: dict = {}

    if (v := _str("SELF_CONTAINER_NAME"))  is not None: overrides["self_container_name"]  = v
    if (v := _str("BACKUP_DEST"))          is not None: overrides["backup_dest"]           = v
    if (v := _str("RESTORE_SNAPSHOT_DIR")) is not None: overrides["restore_snapshot_dir"]  = v
    if (v := _str("BASE_DATA_DIR"))        is not None: overrides["base_data_dir"]         = v
    if (v := _str("WEB_HOST"))             is not None: overrides["web_host"]              = v
    if (v := _int("WEB_PORT"))             is not None: overrides["web_port"]              = v

    # Schedule overrides handled on nested object
    sched_overrides: dict = {}
    if (v := _bool("SCHEDULE_ENABLED")) is not None: sched_overrides["enabled"] = v
    if (v := _str("SCHEDULE_CRON"))     is not None: sched_overrides["cron"]    = v

    if not overrides and not sched_overrides:
        return cfg

    data = cfg.model_dump()
    data.update(overrides)
    if sched_overrides:
        data["schedule"].update(sched_overrides)

    return AppConfig(**data)


# ── Singleton ─────────────────────────────────────────────────────────────────

_config: Optional[AppConfig] = None
_config_path: Optional[str] = None


def load_config(path: Optional[str] = None) -> AppConfig:
    """Load config from disk, then apply env var overrides."""
    global _config, _config_path

    candidates = [path] if path else [p for p in DEFAULT_PATHS if p]

    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            with open(p, "r") as f:
                raw = yaml.safe_load(f) or {}
            cfg = AppConfig(**raw)
            _config_path = str(p)
            _config = _apply_env_overrides(cfg)
            return _config

    print("[config] No config.yaml found — using built-in defaults with env overrides")
    _config = _apply_env_overrides(AppConfig())
    return _config


def get_config() -> AppConfig:
    """Return cached config, loading it on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """Force reload from disk and reapply env overrides."""
    global _config
    _config = None
    return load_config(_config_path)


def get_config_path() -> Optional[str]:
    return _config_path
