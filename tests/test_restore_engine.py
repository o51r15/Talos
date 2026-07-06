"""
Tests for core/restore_engine.py.
All Docker operations and backup primitives are mocked.
"""

import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock, call

from core.models import (
    ContainerInfo, ContainerStatus, ComposeInfo,
    RestoreOptions, BackupOptions, BackupRecord, BackupType,
)


def _cfg(tmp_path):
    from core.config import AppConfig
    return AppConfig(
        backup_dest=str(tmp_path / "backups"),
        restore_snapshot_dir=str(tmp_path / "snapshots"),
        purge_snapshot_on_success=False,
    )


def _make_backup_file(backup_dir: Path, container: str, btype: str, ts_str: str) -> Path:
    """Create a dummy tar.gz backup file in the container backup directory."""
    import tarfile, io
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{container}_{btype}_{ts_str}.tar.gz"
    path = backup_dir / filename
    # Write a minimal valid tar.gz
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz"):
        pass
    path.write_bytes(buf.getvalue())
    return path


def _make_data_backup(tmp_path, container="myapp", ts="2026-07-06_02-00-00"):
    d = tmp_path / "backups" / container
    return _make_backup_file(d, container, "data", ts)


def _make_compose_backup(tmp_path, container="myapp", ts="2026-07-06_02-00-00"):
    d = tmp_path / "backups" / container
    return _make_backup_file(d, container, "compose", ts)


@pytest.fixture
def container(tmp_path):
    data_dir = tmp_path / "docker" / "myapp"
    data_dir.mkdir(parents=True)
    (data_dir / "old.db").write_bytes(b"old data")

    return ContainerInfo(
        id="a" * 64,
        short_id="aaa123",
        name="myapp",
        status=ContainerStatus.RUNNING,
        image="myapp:latest",
        data_dir=str(data_dir),
        compose=ComposeInfo(
            project_name="mystack",
            config_files=[],
            working_dir=str(tmp_path / "docker" / "myapp"),
            discovered=False,
        ),
    )


# ── Safety snapshot ───────────────────────────────────────────────────────────

class TestSafetySnapshot:

    def test_snapshot_taken_before_restore(self, tmp_path, container):
        _make_data_backup(tmp_path)
        cfg = _cfg(tmp_path)

        snapshot_taken = []
        orig_snapshot = None

        def fake_snapshot(c, opts, _log):
            snapshot_taken.append(True)
            return []

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", side_effect=fake_snapshot), \
             patch("core.restore_engine.dc.stop_container", return_value=True), \
             patch("core.restore_engine.dc.start_container", return_value=True), \
             patch("core.restore_engine._restore_data", return_value=True):
            from core.restore_engine import run_restore
            opts = RestoreOptions(
                restore_data=True, restore_compose=False,
                backup_id_data="myapp_data_2026-07-06_02-00-00.tar.gz",
            )
            run_restore(container, opts)

        assert snapshot_taken  # snapshot was called

    def test_aborts_if_snapshot_fails(self, tmp_path, container):
        cfg = _cfg(tmp_path)

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", side_effect=RuntimeError("disk full")):
            from core.restore_engine import run_restore
            opts = RestoreOptions(restore_data=True, backup_id_data="x.tar.gz")
            result = run_restore(container, opts)

        assert result is False


# ── Data restore ──────────────────────────────────────────────────────────────

class TestRestoreData:

    def test_data_restore_clears_existing_files(self, tmp_path, container):
        data_backup = _make_data_backup(tmp_path)
        cfg = _cfg(tmp_path)

        data_dir = Path(container.data_dir)
        (data_dir / "old.db").write_bytes(b"stale data")

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
             patch("core.restore_engine.dc.stop_container", return_value=True), \
             patch("core.restore_engine.dc.start_container", return_value=True):
            from core.restore_engine import run_restore
            opts = RestoreOptions(
                restore_data=True, restore_compose=False,
                backup_id_data=data_backup.name,
            )
            result = run_restore(container, opts)

        assert result is True
        # Old file should be gone
        assert not (data_dir / "old.db").exists()

    def test_data_restore_fails_gracefully_on_missing_backup(self, tmp_path, container):
        cfg = _cfg(tmp_path)

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
             patch("core.restore_engine.dc.stop_container", return_value=True), \
             patch("core.restore_engine.dc.start_container", return_value=True):
            from core.restore_engine import run_restore
            opts = RestoreOptions(
                restore_data=True,
                backup_id_data="nonexistent_data_2026-07-06_02-00-00.tar.gz",
            )
            result = run_restore(container, opts)

        assert result is False


# ── Internal volume restore ───────────────────────────────────────────────────

class TestRestoreInternal:

    def _make_vol_backups(self, tmp_path, container="myapp", ts="2026-07-06_02-00-00", vol_names=None):
        """Create per-volume backup files and return the first one's name."""
        vol_names = vol_names or ["myapp_data"]
        d = tmp_path / "backups" / container
        files = []
        for v in vol_names:
            safe = v.replace("/", "-").replace(":", "-")
            files.append(_make_backup_file(d, container, f"internal_vol-{safe}", ts))
        return files

    def test_restores_each_named_volume(self, tmp_path, container):
        ts = "2026-07-06_02-00-00"
        vol_files = self._make_vol_backups(tmp_path, vol_names=["vol1", "vol2"], ts=ts)
        cfg = _cfg(tmp_path)
        restore_vol = MagicMock(return_value=True)

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine.dc.restore_named_volume", restore_vol):
            from core.restore_engine import _restore_internal
            result = _restore_internal(container, vol_files[0].name, lambda m, l: None)

        assert result is True
        assert restore_vol.call_count == 2
        called_vols = {c.args[0] for c in restore_vol.call_args_list}
        assert "vol1" in called_vols
        assert "vol2" in called_vols

    def test_uses_timestamp_from_any_file_in_set(self, tmp_path, container):
        """Passing the second file in a set should still restore ALL volumes."""
        ts = "2026-07-06_02-00-00"
        vol_files = self._make_vol_backups(tmp_path, vol_names=["vol1", "vol2"], ts=ts)
        cfg = _cfg(tmp_path)
        restore_vol = MagicMock(return_value=True)

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine.dc.restore_named_volume", restore_vol):
            from core.restore_engine import _restore_internal
            # Pass the SECOND file — should still find both
            result = _restore_internal(container, vol_files[1].name, lambda m, l: None)

        assert result is True
        assert restore_vol.call_count == 2

    def test_returns_false_when_no_files_at_timestamp(self, tmp_path, container):
        cfg = _cfg(tmp_path)
        backup_dir = tmp_path / "backups" / "myapp"
        backup_dir.mkdir(parents=True)

        with patch("core.restore_engine.get_config", return_value=cfg):
            from core.restore_engine import _restore_internal
            result = _restore_internal(
                container,
                "myapp_internal_vol-data_2026-07-06_02-00-00.tar.gz",
                lambda m, l: None,
            )

        assert result is False

    def test_partial_failure_returns_true_if_some_succeed(self, tmp_path, container):
        ts = "2026-07-06_02-00-00"
        vol_files = self._make_vol_backups(tmp_path, vol_names=["vol1", "vol2"], ts=ts)
        cfg = _cfg(tmp_path)

        # vol1 succeeds, vol2 fails
        def restore_side_effect(vol_name, *args):
            return vol_name == "vol1"

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine.dc.restore_named_volume", side_effect=restore_side_effect):
            from core.restore_engine import _restore_internal
            result = _restore_internal(container, vol_files[0].name, lambda m, l: None)

        assert result is True  # at least one succeeded


# ── Snapshot purge ────────────────────────────────────────────────────────────

class TestSnapshotPurge:

    def test_purges_snapshot_when_configured(self, tmp_path, container):
        from core.config import AppConfig
        cfg = AppConfig(
            backup_dest=str(tmp_path / "backups"),
            restore_snapshot_dir=str(tmp_path / "snapshots"),
            purge_snapshot_on_success=True,
        )

        snap_file = tmp_path / "snapshots" / "myapp" / "myapp_data_2026-07-06_02-00-00.tar.gz"
        snap_file.parent.mkdir(parents=True)
        snap_file.write_bytes(b"snapshot")

        snap_record = BackupRecord.from_path(str(snap_file))
        snap_record.is_restore_snapshot = True

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", return_value=[snap_record]), \
             patch("core.restore_engine.dc.stop_container", return_value=True), \
             patch("core.restore_engine.dc.start_container", return_value=True), \
             patch("core.restore_engine._restore_data", return_value=True):
            from core.restore_engine import run_restore
            opts = RestoreOptions(restore_data=True, backup_id_data="x_data_2026-07-06_02-00-00.tar.gz")
            run_restore(container, opts)

        assert not snap_file.exists()

    def test_keeps_snapshot_when_not_configured(self, tmp_path, container):
        cfg = _cfg(tmp_path)

        snap_file = tmp_path / "snapshots" / "myapp" / "myapp_data_2026-07-06_02-00-00.tar.gz"
        snap_file.parent.mkdir(parents=True)
        snap_file.write_bytes(b"snapshot")

        snap_record = BackupRecord.from_path(str(snap_file))

        with patch("core.restore_engine.get_config", return_value=cfg), \
             patch("core.restore_engine._take_safety_snapshot", return_value=[snap_record]), \
             patch("core.restore_engine.dc.stop_container", return_value=True), \
             patch("core.restore_engine.dc.start_container", return_value=True), \
             patch("core.restore_engine._restore_data", return_value=True):
            from core.restore_engine import run_restore
            opts = RestoreOptions(restore_data=True, backup_id_data="x_data_2026-07-06_02-00-00.tar.gz")
            run_restore(container, opts)

        assert snap_file.exists()
