"""
Tests for core/config.py — YAML loading and env var override layer.
"""

import os
import pytest
from unittest.mock import patch


class TestConfigLoading:

    def test_loads_from_explicit_path(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("backup_dest: /mnt/backups\nweb_port: 9999\n")

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.backup_dest == "/mnt/backups"
        assert cfg.web_port == 9999

    def test_defaults_when_file_missing(self):
        from core.config import load_config
        cfg = load_config("/nonexistent/config.yaml")
        assert cfg.backup_dest == "/backups"
        assert cfg.web_port == 8000

    def test_retention_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")   # empty file

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.retention.keep_last == 7
        assert cfg.retention.max_age_days == 30

    def test_retention_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("retention:\n  keep_last: 14\n  max_age_days: 60\n")

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.retention.keep_last == 14
        assert cfg.retention.max_age_days == 60

    def test_schedule_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.schedule.enabled is False
        assert cfg.schedule.cron == "0 2 * * *"

    def test_schedule_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule:\n  enabled: true\n  cron: '0 3 * * 0'\n")

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.schedule.enabled is True
        assert cfg.schedule.cron == "0 3 * * 0"

    def test_auth_disabled_by_default(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")

        from core.config import load_config
        cfg = load_config(str(cfg_file))
        assert cfg.auth.enabled is False


class TestEnvVarOverrides:

    def test_self_container_name(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("self_container_name: from-yaml\n")
        with patch.dict(os.environ, {"SELF_CONTAINER_NAME": "from-env"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.self_container_name == "from-env"

    def test_backup_dest(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        with patch.dict(os.environ, {"BACKUP_DEST": "/mnt/override"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.backup_dest == "/mnt/override"

    def test_restore_snapshot_dir(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        with patch.dict(os.environ, {"RESTORE_SNAPSHOT_DIR": "/mnt/snap"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.restore_snapshot_dir == "/mnt/snap"

    def test_web_port_as_int(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        with patch.dict(os.environ, {"WEB_PORT": "9000"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.web_port == 9000
        assert isinstance(cfg.web_port, int)

    def test_web_port_invalid_ignored(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("web_port: 8000\n")
        with patch.dict(os.environ, {"WEB_PORT": "not-a-number"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.web_port == 8000   # falls back to YAML value

    def test_empty_env_var_does_not_override(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("self_container_name: from-yaml\n")
        with patch.dict(os.environ, {"SELF_CONTAINER_NAME": "   "}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.self_container_name == "from-yaml"

    def test_schedule_enabled_true(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule:\n  enabled: false\n")
        with patch.dict(os.environ, {"SCHEDULE_ENABLED": "true"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.schedule.enabled is True

    def test_schedule_enabled_1(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        with patch.dict(os.environ, {"SCHEDULE_ENABLED": "1"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.schedule.enabled is True

    def test_schedule_enabled_false(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule:\n  enabled: true\n")
        with patch.dict(os.environ, {"SCHEDULE_ENABLED": "false"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.schedule.enabled is False

    def test_schedule_cron(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        with patch.dict(os.environ, {"SCHEDULE_CRON": "0 3 * * 0"}):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.schedule.cron == "0 3 * * 0"

    def test_env_overrides_all_together(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        env = {
            "SELF_CONTAINER_NAME": "talos",
            "BACKUP_DEST": "/mnt/backups",
            "WEB_PORT": "8080",
            "SCHEDULE_ENABLED": "true",
        }
        with patch.dict(os.environ, env):
            from core.config import load_config
            cfg = load_config(str(cfg_file))
        assert cfg.self_container_name == "talos"
        assert cfg.backup_dest == "/mnt/backups"
        assert cfg.web_port == 8080
        assert cfg.schedule.enabled is True


class TestGetConfig:

    def test_get_config_returns_cached(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("web_port: 7777\n")

        from core.config import load_config, get_config
        load_config(str(cfg_file))
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2   # same object — cached

    def test_reload_config_returns_fresh(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("web_port: 7777\n")

        from core.config import load_config, reload_config
        load_config(str(cfg_file))

        cfg_file.write_text("web_port: 8888\n")
        cfg = reload_config()
        assert cfg.web_port == 8888
