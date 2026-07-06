"""
Config routes.

GET  /api/config          — return current effective config (YAML + env overrides applied)
POST /api/config/reload   — reload config from disk and reapply env overrides
GET  /api/config/schedule — scheduler status (next run, enabled, cron)
"""

from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from core.config import get_config, reload_config, get_config_path

router = APIRouter()


class SchedulerStatus(BaseModel):
    enabled: bool
    cron: str
    running: bool
    next_run: Optional[str] = None


class ConfigSummary(BaseModel):
    config_path: Optional[str]
    backup_dest: str
    restore_snapshot_dir: str
    base_data_dir: str
    self_container_name: Optional[str]
    web_host: str
    web_port: int
    retention_keep_last: int
    retention_max_age_days: int
    purge_snapshot_on_success: bool
    schedule_enabled: bool
    schedule_cron: str
    auth_enabled: bool


@router.get("", response_model=ConfigSummary)
async def get_config_summary():
    """Return the current effective configuration (read-only, no secrets)."""
    cfg = get_config()
    return ConfigSummary(
        config_path=get_config_path(),
        backup_dest=cfg.backup_dest,
        restore_snapshot_dir=cfg.restore_snapshot_dir,
        base_data_dir=cfg.base_data_dir,
        self_container_name=cfg.self_container_name,
        web_host=cfg.web_host,
        web_port=cfg.web_port,
        retention_keep_last=cfg.retention.keep_last,
        retention_max_age_days=cfg.retention.max_age_days,
        purge_snapshot_on_success=cfg.purge_snapshot_on_success,
        schedule_enabled=cfg.schedule.enabled,
        schedule_cron=cfg.schedule.cron,
        auth_enabled=cfg.auth.enabled,
    )


@router.post("/reload")
async def reload_config_endpoint():
    """Reload config.yaml from disk and reapply env overrides."""
    cfg = reload_config()
    return {
        "reloaded": True,
        "config_path": get_config_path(),
        "backup_dest": cfg.backup_dest,
        "schedule_enabled": cfg.schedule.enabled,
    }


@router.get("/schedule", response_model=SchedulerStatus)
async def scheduler_status():
    """Return current scheduler state and next scheduled run time."""
    from core.scheduler import get_next_run, is_running
    cfg = get_config()
    return SchedulerStatus(
        enabled=cfg.schedule.enabled,
        cron=cfg.schedule.cron,
        running=is_running(),
        next_run=get_next_run(),
    )
