"""
Tests for core/models.py — BackupRecord.from_path filename parsing.
These are pure unit tests with no I/O beyond tmp_path file creation.
"""

import pytest
from datetime import datetime
from pathlib import Path
from core.models import BackupRecord, BackupType


def make_file(directory: Path, name: str, content: bytes = b"x") -> Path:
    f = directory / name
    f.write_bytes(content)
    return f


class TestBackupRecordFromPath:

    # ── Happy paths ────────────────────────────────────────────────────────────

    def test_data_backup(self, tmp_path):
        f = make_file(tmp_path, "myapp_data_2026-07-06_02-00-00.tar.gz", b"x" * 1024)
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.container_name == "myapp"
        assert r.backup_type == BackupType.DATA
        assert r.timestamp == datetime(2026, 7, 6, 2, 0, 0)
        assert r.volume_name is None
        assert r.filename == "myapp_data_2026-07-06_02-00-00.tar.gz"

    def test_compose_backup(self, tmp_path):
        f = make_file(tmp_path, "myapp_compose_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.backup_type == BackupType.COMPOSE
        assert r.volume_name is None

    def test_internal_backup_no_volume(self, tmp_path):
        # Legacy docker-save format — no vol- in name
        f = make_file(tmp_path, "myapp_internal_2026-07-06_02-00-00.tar")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.backup_type == BackupType.INTERNAL
        assert r.volume_name is None

    def test_internal_vol_backup_simple(self, tmp_path):
        f = make_file(tmp_path, "myapp_internal_vol-mydata_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.backup_type == BackupType.INTERNAL
        assert r.volume_name == "mydata"

    def test_internal_vol_backup_hyphens(self, tmp_path):
        f = make_file(tmp_path, "myapp_internal_vol-my-complex-vol_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.volume_name == "my-complex-vol"

    def test_container_name_with_hyphens(self, tmp_path):
        f = make_file(tmp_path, "my-app-name_data_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.container_name == "my-app-name"

    def test_container_name_with_underscores(self, tmp_path):
        f = make_file(tmp_path, "my_app_data_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.container_name == "my_app"

    # ── Timestamps ────────────────────────────────────────────────────────────

    def test_timestamp_parsed_correctly(self, tmp_path):
        f = make_file(tmp_path, "app_data_2025-12-31_23-59-59.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r.timestamp == datetime(2025, 12, 31, 23, 59, 59)

    # ── Sizes ─────────────────────────────────────────────────────────────────

    def test_size_bytes_populated(self, tmp_path):
        content = b"x" * 2048
        f = make_file(tmp_path, "app_data_2026-07-06_02-00-00.tar.gz", content)
        r = BackupRecord.from_path(str(f))
        assert r.size_bytes == 2048

    def test_size_human_kb(self, tmp_path):
        f = make_file(tmp_path, "app_data_2026-07-06_02-00-00.tar.gz", b"x" * 2048)
        r = BackupRecord.from_path(str(f))
        assert "KB" in r.size_human

    def test_size_human_mb(self, tmp_path):
        f = make_file(tmp_path, "app_data_2026-07-06_02-00-00.tar.gz", b"x" * (2 * 1024 * 1024))
        r = BackupRecord.from_path(str(f))
        assert "MB" in r.size_human

    # ── Snapshot detection ────────────────────────────────────────────────────

    def test_snapshot_flag_from_path(self, tmp_path):
        snap_dir = tmp_path / "restore_snapshot" / "myapp"
        snap_dir.mkdir(parents=True)
        f = make_file(snap_dir, "myapp_data_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r is not None
        assert r.is_restore_snapshot is True

    def test_normal_backup_not_snapshot(self, tmp_path):
        f = make_file(tmp_path, "myapp_data_2026-07-06_02-00-00.tar.gz")
        r = BackupRecord.from_path(str(f))
        assert r.is_restore_snapshot is False

    # ── Invalid filenames ─────────────────────────────────────────────────────

    def test_plain_text_file_returns_none(self, tmp_path):
        f = make_file(tmp_path, "readme.txt")
        assert BackupRecord.from_path(str(f)) is None

    def test_wrong_extension_returns_none(self, tmp_path):
        f = make_file(tmp_path, "myapp_data_2026-07-06_02-00-00.zip")
        assert BackupRecord.from_path(str(f)) is None

    def test_missing_timestamp_returns_none(self, tmp_path):
        f = make_file(tmp_path, "myapp_data.tar.gz")
        assert BackupRecord.from_path(str(f)) is None

    def test_unknown_type_returns_none(self, tmp_path):
        f = make_file(tmp_path, "myapp_logs_2026-07-06_02-00-00.tar.gz")
        assert BackupRecord.from_path(str(f)) is None

    def test_nonexistent_file_size_is_zero(self, tmp_path):
        path = str(tmp_path / "myapp_data_2026-07-06_02-00-00.tar.gz")
        # File doesn't exist — should still parse but size = 0
        r = BackupRecord.from_path(path)
        assert r is not None
        assert r.size_bytes == 0

    # ── ID field ──────────────────────────────────────────────────────────────

    def test_id_equals_filename(self, tmp_path):
        name = "myapp_data_2026-07-06_02-00-00.tar.gz"
        f = make_file(tmp_path, name)
        r = BackupRecord.from_path(str(f))
        assert r.id == name
