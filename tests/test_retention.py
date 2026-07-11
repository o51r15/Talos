"""
Tests for core/retention.py — retention policy logic.
Uses real filesystem via tmp_path; mocks get_config to avoid singleton issues.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


def _make_backup(directory: Path, container: str, ts: datetime, btype: str = "data") -> Path:
    """Write a dummy backup file with a parseable filename."""
    ts_str = ts.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{container}_{btype}_{ts_str}.tar.gz"
    f = directory / filename
    f.write_bytes(b"x" * 128)
    return f


def _config(tmp_path, keep_last=7, max_age_days=0):
    from core.config import AppConfig, RetentionConfig
    return AppConfig(
        backup_dest=str(tmp_path / "backups"),
        restore_snapshot_dir=str(tmp_path / "snapshots"),
        retention=RetentionConfig(keep_last=keep_last, max_age_days=max_age_days),
    )


class TestRunRetention:

    def test_removes_excess_by_count(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        for i in range(10):
            _make_backup(container_dir, "myapp", now - timedelta(days=i))

        cfg = _config(tmp_path, keep_last=3)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        assert deleted == 7
        assert len(list(container_dir.iterdir())) == 3

    def test_keeps_newest_when_pruning(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        timestamps = [now - timedelta(days=i) for i in range(5)]
        for ts in timestamps:
            _make_backup(container_dir, "myapp", ts)

        cfg = _config(tmp_path, keep_last=2)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            run_retention("myapp")

        remaining = list(container_dir.iterdir())
        assert len(remaining) == 2
        # The two newest should be kept
        remaining_ts = sorted(
            [f.name for f in remaining],
            reverse=True,
        )
        assert "2026-07-06" in remaining_ts[0]
        assert "2026-07-05" in remaining_ts[1]

    def test_removes_by_age(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        _make_backup(container_dir, "myapp", now - timedelta(days=5))    # recent → keep
        _make_backup(container_dir, "myapp", now - timedelta(days=45))   # too old → delete

        cfg = _config(tmp_path, keep_last=100, max_age_days=30)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        assert deleted == 1
        assert len(list(container_dir.iterdir())) == 1

    def test_age_disabled_when_zero(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        _make_backup(container_dir, "myapp", now - timedelta(days=200))  # very old

        cfg = _config(tmp_path, keep_last=100, max_age_days=0)  # 0 = disabled
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        assert deleted == 0
        assert len(list(container_dir.iterdir())) == 1

    def test_types_kept_independently(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        for i in range(5):
            _make_backup(container_dir, "myapp", now - timedelta(days=i), "data")
            _make_backup(container_dir, "myapp", now - timedelta(days=i), "compose")

        cfg = _config(tmp_path, keep_last=2)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        # 3 old data + 3 old compose = 6 deleted; 2 data + 2 compose = 4 kept
        assert deleted == 6
        assert len(list(container_dir.iterdir())) == 4

    def test_skips_restore_snapshot_dir(self, tmp_path):
        """
        Snapshot dir inside backup_dest must not be touched by retention.
        Uses resolve()-based comparison so a container named 'restore_snapshot'
        is not incorrectly excluded.
        """
        snap_dir = tmp_path / "backups" / "restore_snapshot" / "myapp"
        snap_dir.mkdir(parents=True)

        # Also create a real container dir to confirm it IS cleaned
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        for i in range(10):
            _make_backup(snap_dir, "myapp", now - timedelta(days=i))
            _make_backup(container_dir, "myapp", now - timedelta(days=i))

        from core.config import AppConfig, RetentionConfig
        cfg = AppConfig(
            backup_dest=str(tmp_path / "backups"),
            restore_snapshot_dir=str(tmp_path / "backups" / "restore_snapshot"),
            retention=RetentionConfig(keep_last=2, max_age_days=0),
        )
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention()   # all containers

        # Snapshots untouched
        assert len(list(snap_dir.iterdir())) == 10
        # Real container backups trimmed to keep_last=2
        assert len(list(container_dir.iterdir())) == 2

    def test_no_op_on_empty_directory(self, tmp_path):
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        cfg = _config(tmp_path, keep_last=3)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        assert deleted == 0

    def test_nonexistent_container_dir_no_error(self, tmp_path):
        cfg = _config(tmp_path, keep_last=3)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("doesnotexist")

        assert deleted == 0

    def test_all_containers_when_no_name_given(self, tmp_path):
        now = datetime(2026, 7, 6, 12, 0, 0)

        for container in ("app1", "app2"):
            d = tmp_path / "backups" / container
            d.mkdir(parents=True)
            for i in range(5):
                _make_backup(d, container, now - timedelta(days=i))

        cfg = _config(tmp_path, keep_last=2)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention()   # no container name = all

        assert deleted == 6   # 3 from app1 + 3 from app2

    def test_missing_backup_root_no_error(self, tmp_path):
        """First run before any backup exists: all-container mode must not crash."""
        cfg = _config(tmp_path, keep_last=3)   # backup_dest never created
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention()   # all-container mode iterates the root

        assert deleted == 0

    def test_internal_volumes_kept_per_volume(self, tmp_path):
        """
        keep_last must mean 'backup runs' for internal backups too.
        3 volumes × 5 runs with keep_last=2 keeps 2 runs of EACH volume
        (6 files), not 2 files total.
        """
        container_dir = tmp_path / "backups" / "myapp"
        container_dir.mkdir(parents=True)

        now = datetime(2026, 7, 6, 12, 0, 0)
        vols = ("db", "cache", "media")
        for i in range(5):                       # 5 backup runs
            for v in vols:                       # 3 volumes per run
                _make_backup(container_dir, "myapp",
                             now - timedelta(days=i), f"internal_vol-{v}")

        cfg = _config(tmp_path, keep_last=2)
        with patch("core.retention.get_config", return_value=cfg):
            from core.retention import run_retention
            deleted = run_retention("myapp")

        remaining = list(container_dir.iterdir())
        # 2 runs × 3 volumes kept; 3 runs × 3 volumes deleted
        assert len(remaining) == 6
        assert deleted == 9
        # Each volume retains exactly its own 2 newest files
        for v in vols:
            per_vol = [f for f in remaining if f"vol-{v}_" in f.name]
            assert len(per_vol) == 2
