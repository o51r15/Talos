"""
Tests for core/backup_engine.py.
All Docker operations are mocked — no Docker daemon required.
"""

import pytest
import tarfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from core.models import (
    ContainerInfo, ContainerStatus, ComposeInfo,
    BackupOptions, BackupRecord, BackupType,
)


def _cfg(tmp_path):
    from core.config import AppConfig
    return AppConfig(
        backup_dest=str(tmp_path / "backups"),
        restore_snapshot_dir=str(tmp_path / "snapshots"),
        base_data_dir=str(tmp_path / "docker"),
    )


def _make_data_dir(tmp_path, container_name="myapp") -> Path:
    d = tmp_path / "docker" / container_name
    d.mkdir(parents=True)
    (d / "app.db").write_bytes(b"data" * 512)
    return d


@pytest.fixture
def full_container(tmp_path):
    data_dir = _make_data_dir(tmp_path)
    return ContainerInfo(
        id="abc" * 21 + "a",
        short_id="abc123",
        name="myapp",
        status=ContainerStatus.RUNNING,
        image="myapp:latest",
        data_dir=str(data_dir),
        has_external_mounts=True,
        has_internal_volumes=False,
        compose=ComposeInfo(
            project_name="mystack",
            config_files=[str(tmp_path / "docker-compose.yml")],
            working_dir=str(tmp_path),
            shared_containers=[],
            discovered=True,
        ),
    )


# ── Data backup ───────────────────────────────────────────────────────────────

class TestDataBackup:

    def test_creates_tar_file(self, tmp_path, full_container):
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        assert len(records) == 1
        assert records[0].backup_type == BackupType.DATA
        assert Path(records[0].filepath).exists()

    def test_archive_contains_data_files(self, tmp_path, full_container):
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        with tarfile.open(records[0].filepath, "r:gz") as tar:
            names = tar.getnames()
        assert any("app.db" in n for n in names)

    def test_skips_data_backup_when_no_data_dir(self, tmp_path, full_container):
        full_container.data_dir = None
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        logs = []
        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts, log_cb=lambda m, l: logs.append((m, l)))

        assert len(records) == 0
        assert any("No data directory" in m for m, l in logs)


# ── Container lifecycle ───────────────────────────────────────────────────────

class TestContainerLifecycle:

    def test_running_container_is_stopped_and_restarted(self, tmp_path, full_container):
        cfg = _cfg(tmp_path)
        stop = MagicMock(return_value=True)
        start = MagicMock(return_value=True)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", stop), \
             patch("core.backup_engine.dc.start_container", start):
            from core.backup_engine import run_backup
            run_backup(full_container, opts)

        stop.assert_called_once_with("myapp")
        start.assert_called_once_with("myapp")

    def test_stopped_container_not_stopped_or_restarted(self, tmp_path, full_container):
        full_container.status = ContainerStatus.STOPPED
        cfg = _cfg(tmp_path)
        stop = MagicMock(return_value=True)
        start = MagicMock(return_value=True)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", stop), \
             patch("core.backup_engine.dc.start_container", start):
            from core.backup_engine import run_backup
            run_backup(full_container, opts)

        stop.assert_not_called()
        start.assert_not_called()

    def test_container_restarted_even_if_backup_fails(self, tmp_path, full_container):
        cfg = _cfg(tmp_path)
        start = MagicMock(return_value=True)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", start), \
             patch("core.backup_engine.tarfile.open", side_effect=IOError("disk full")):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        start.assert_called_once_with("myapp")
        assert len(records) == 0

    def test_raises_if_stop_fails(self, tmp_path, full_container):
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=True, backup_compose=False, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=False), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            with pytest.raises(RuntimeError, match="Failed to stop"):
                run_backup(full_container, opts)


# ── Compose backup ────────────────────────────────────────────────────────────

class TestComposeBackup:

    def test_compose_backup_created(self, tmp_path, full_container):
        # Create the compose file
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("services:\n  myapp:\n    image: myapp\n")
        full_container.compose.config_files = [str(compose_file)]

        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=False, backup_compose=True, backup_internal=False)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        assert len(records) == 1
        assert records[0].backup_type == BackupType.COMPOSE

    def test_skips_compose_when_none_discovered(self, tmp_path, full_container):
        full_container.compose = None
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=False, backup_compose=True, backup_internal=False)
        logs = []

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts, log_cb=lambda m, l: logs.append((m, l)))

        assert len(records) == 0
        assert any("No compose files" in m for m, l in logs)


# ── Internal / named volumes ──────────────────────────────────────────────────

class TestInternalBackup:

    def test_skips_internal_when_no_volumes(self, tmp_path, full_container):
        full_container.has_internal_volumes = False
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=False, backup_compose=False, backup_internal=True)
        logs = []

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts, log_cb=lambda m, l: logs.append((m, l)))

        assert len(records) == 0
        assert any("No named volumes" in m for m, l in logs)

    def test_backs_up_each_volume(self, tmp_path, full_container):
        full_container.has_internal_volumes = True
        (tmp_path / "backups" / "myapp").mkdir(parents=True)

        volumes = [
            {"name": "myapp_data", "destination": "/data", "read_write": True},
            {"name": "myapp_cache", "destination": "/cache", "read_write": True},
        ]
        cfg = _cfg(tmp_path)
        opts = BackupOptions(backup_data=False, backup_compose=False, backup_internal=True)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True), \
             patch("core.backup_engine.dc.list_named_volumes", return_value=volumes), \
             patch("core.backup_engine.dc.backup_named_volume", return_value=True) as bv:
            from core.backup_engine import run_backup

            # Create dummy output files that backup_named_volume would normally create
            backup_dir = tmp_path / "backups" / "myapp"
            ts_pattern = datetime.now().strftime("%Y-%m-%d")
            # We'll check that backup_named_volume was called twice
            run_backup(full_container, opts)

        assert bv.call_count == 2
        # Each call should be for a different volume
        called_vols = {c.args[0] for c in bv.call_args_list}
        assert "myapp_data" in called_vols
        assert "myapp_cache" in called_vols


# ── Compose siblings ──────────────────────────────────────────────────────────

class TestComposeSiblings:

    def test_siblings_backed_up_when_flag_set(self, tmp_path, full_container):
        full_container.compose.shared_containers = ["sibling1"]
        _make_data_dir(tmp_path, "sibling1")

        sibling = ContainerInfo(
            id="s" * 64,
            short_id="sib123",
            name="sibling1",
            status=ContainerStatus.RUNNING,
            image="sibling:latest",
            data_dir=str(tmp_path / "docker" / "sibling1"),
        )

        cfg = _cfg(tmp_path)
        opts = BackupOptions(
            backup_data=True, backup_compose=False, backup_internal=False,
            include_compose_siblings=True,
        )

        get_container_mock = MagicMock(side_effect=lambda name: sibling if name == "sibling1" else None)
        enrich_mock = MagicMock(side_effect=lambda containers: containers)

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True), \
             patch("core.backup_engine.dc.get_container", get_container_mock), \
             patch("core.backup_engine.enrich_with_compose", enrich_mock):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        # Should have records for both primary and sibling
        container_names = {r.container_name for r in records}
        assert "myapp" in container_names
        assert "sibling1" in container_names

    def test_self_sibling_skipped(self, tmp_path, full_container):
        self_container = ContainerInfo(
            id="s" * 64,
            short_id="self123",
            name="backup-manager",
            status=ContainerStatus.RUNNING,
            image="talos:latest",
            is_self=True,
        )
        full_container.compose.shared_containers = ["backup-manager"]

        cfg = _cfg(tmp_path)
        opts = BackupOptions(
            backup_data=True, backup_compose=False, backup_internal=False,
            include_compose_siblings=True,
        )

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True), \
             patch("core.backup_engine.dc.get_container", return_value=self_container), \
             patch("core.backup_engine.enrich_with_compose", side_effect=lambda c: c):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        container_names = {r.container_name for r in records}
        assert "backup-manager" not in container_names

    def test_siblings_not_backed_up_when_flag_false(self, tmp_path, full_container):
        full_container.compose.shared_containers = ["sibling1"]
        cfg = _cfg(tmp_path)
        opts = BackupOptions(
            backup_data=True, backup_compose=False, backup_internal=False,
            include_compose_siblings=False,
        )
        get_container_mock = MagicMock()

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True), \
             patch("core.backup_engine.dc.get_container", get_container_mock):
            from core.backup_engine import run_backup
            run_backup(full_container, opts)

        get_container_mock.assert_not_called()

    def test_sibling_flag_prevents_recursion(self, tmp_path, full_container):
        """Siblings are backed up with include_compose_siblings=False to prevent loops."""
        full_container.compose.shared_containers = ["sibling1"]
        _make_data_dir(tmp_path, "sibling1")

        sibling = ContainerInfo(
            id="s" * 64,
            short_id="sib123",
            name="sibling1",
            status=ContainerStatus.RUNNING,
            image="sibling:latest",
            data_dir=str(tmp_path / "docker" / "sibling1"),
            compose=ComposeInfo(
                project_name="mystack",
                shared_containers=["myapp"],  # would cause recursion if not guarded
            ),
        )

        cfg = _cfg(tmp_path)
        opts = BackupOptions(
            backup_data=True, backup_compose=False, backup_internal=False,
            include_compose_siblings=True,
        )

        call_count = 0
        original_get = lambda name: sibling if name == "sibling1" else None

        with patch("core.backup_engine.get_config", return_value=cfg), \
             patch("core.backup_engine.dc.stop_container", return_value=True), \
             patch("core.backup_engine.dc.start_container", return_value=True), \
             patch("core.backup_engine.dc.get_container", side_effect=original_get), \
             patch("core.backup_engine.enrich_with_compose", side_effect=lambda c: c):
            from core.backup_engine import run_backup
            records = run_backup(full_container, opts)

        # myapp + sibling1, but NOT myapp again due to recursion guard
        assert len([r for r in records if r.container_name == "myapp"]) == 1
