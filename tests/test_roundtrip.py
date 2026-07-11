"""
End-to-end round-trip: backup (new multi-source manifest format) then restore.
Docker stop/start is mocked; the filesystem work is real, so this proves the
archive layout and manifest restore actually reconstruct the data.
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from core.models import (
    ContainerInfo, ContainerStatus, DataSource, MountInfo,
    BackupOptions, RestoreOptions,
)


def _cfg(tmp_path):
    from core.config import AppConfig
    return AppConfig(
        backup_dest=str(tmp_path / "backups"),
        restore_snapshot_dir=str(tmp_path / "snapshots"),
        base_data_dir=str(tmp_path / "docker"),
    )


def _container(tmp_path, sources):
    return ContainerInfo(
        id="a" * 64, short_id="aaa123", name="myapp",
        status=ContainerStatus.RUNNING, image="myapp:latest",
        data_dir=sources[0].host_path if sources else None,
        data_sources=sources,
        has_external_mounts=True,
    )


def test_single_source_roundtrip(tmp_path):
    data = tmp_path / "appdata"
    data.mkdir()
    (data / "file.txt").write_text("hello")
    (data / "sub").mkdir()
    (data / "sub" / "nested.bin").write_bytes(b"\x00\x01\x02")

    src = DataSource(host_path=str(data), destination="/data", method="bind")
    c = _container(tmp_path, [src])
    cfg = _cfg(tmp_path)

    with patch("core.backup_engine.get_config", return_value=cfg), \
         patch("core.backup_engine.dc.stop_container", return_value=True), \
         patch("core.backup_engine.dc.start_container", return_value=True):
        from core.backup_engine import run_backup
        records = run_backup(c, BackupOptions(backup_data=True, backup_compose=False))

    assert len(records) == 1
    backup_name = records[0].filename

    # Wipe and mutate the data dir
    (data / "file.txt").write_text("CORRUPTED")
    (data / "extra.txt").write_text("should be gone after restore")

    with patch("core.restore_engine.get_config", return_value=cfg), \
         patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
         patch("core.restore_engine.dc.stop_container", return_value=True), \
         patch("core.restore_engine.dc.start_container", return_value=True):
        from core.restore_engine import run_restore
        result = run_restore(c, RestoreOptions(
            restore_data=True, restore_compose=False, backup_id_data=backup_name,
        ))

    assert result is True
    assert (data / "file.txt").read_text() == "hello"          # restored
    assert (data / "sub" / "nested.bin").read_bytes() == b"\x00\x01\x02"
    assert not (data / "extra.txt").exists()                   # wiped


def test_multi_source_roundtrip(tmp_path):
    config_dir = tmp_path / "config"; config_dir.mkdir()
    data_dir = tmp_path / "data"; data_dir.mkdir()
    (config_dir / "settings.yml").write_text("key: value")
    (data_dir / "db.sqlite").write_bytes(b"DBDATA")

    sources = [
        DataSource(host_path=str(config_dir), destination="/config", method="bind"),
        DataSource(host_path=str(data_dir), destination="/var/lib/app", method="bind"),
    ]
    c = _container(tmp_path, sources)
    cfg = _cfg(tmp_path)

    with patch("core.backup_engine.get_config", return_value=cfg), \
         patch("core.backup_engine.dc.stop_container", return_value=True), \
         patch("core.backup_engine.dc.start_container", return_value=True):
        from core.backup_engine import run_backup
        records = run_backup(c, BackupOptions(backup_data=True, backup_compose=False))

    backup_name = records[0].filename

    # Corrupt both source dirs
    (config_dir / "settings.yml").write_text("WRONG")
    (data_dir / "db.sqlite").write_bytes(b"WRONG")

    with patch("core.restore_engine.get_config", return_value=cfg), \
         patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
         patch("core.restore_engine.dc.stop_container", return_value=True), \
         patch("core.restore_engine.dc.start_container", return_value=True):
        from core.restore_engine import run_restore
        result = run_restore(c, RestoreOptions(
            restore_data=True, restore_compose=False, backup_id_data=backup_name,
        ))

    assert result is True
    # Both sources restored to their correct separate locations
    assert (config_dir / "settings.yml").read_text() == "key: value"
    assert (data_dir / "db.sqlite").read_bytes() == b"DBDATA"


def test_restore_follows_moved_bind_mount(tmp_path):
    """
    If the bind mount moved since backup, restore should target the CURRENT
    live host path (matched by in-container destination), not the old one.
    """
    old_dir = tmp_path / "old_location"; old_dir.mkdir()
    (old_dir / "data.txt").write_text("original")

    src = DataSource(host_path=str(old_dir), destination="/data", method="bind")
    c = _container(tmp_path, [src])
    cfg = _cfg(tmp_path)

    with patch("core.backup_engine.get_config", return_value=cfg), \
         patch("core.backup_engine.dc.stop_container", return_value=True), \
         patch("core.backup_engine.dc.start_container", return_value=True):
        from core.backup_engine import run_backup
        records = run_backup(c, BackupOptions(backup_data=True, backup_compose=False))
    backup_name = records[0].filename

    # The container now maps /data to a NEW host path
    new_dir = tmp_path / "new_location"; new_dir.mkdir()
    c_moved = _container(tmp_path, [
        DataSource(host_path=str(new_dir), destination="/data", method="bind"),
    ])

    with patch("core.restore_engine.get_config", return_value=cfg), \
         patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
         patch("core.restore_engine.dc.stop_container", return_value=True), \
         patch("core.restore_engine.dc.start_container", return_value=True):
        from core.restore_engine import run_restore
        result = run_restore(c_moved, RestoreOptions(
            restore_data=True, restore_compose=False, backup_id_data=backup_name,
        ))

    assert result is True
    # Data restored to the NEW location (where the container reads from now)
    assert (new_dir / "data.txt").read_text() == "original"


def test_file_bind_mount_roundtrip(tmp_path):
    """A single-file bind mount (e.g. ./config.yml:/app/config.yml) round-trips."""
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("key: original")
    data_dir = tmp_path / "appdata"
    data_dir.mkdir()
    (data_dir / "db.bin").write_bytes(b"DB")

    sources = [
        DataSource(host_path=str(data_dir), destination="/data", method="bind", kind="dir"),
        DataSource(host_path=str(cfg_file), destination="/app/config.yml",
                   method="bind", kind="file"),
    ]
    c = _container(tmp_path, sources)
    cfg = _cfg(tmp_path)

    with patch("core.backup_engine.get_config", return_value=cfg), \
         patch("core.backup_engine.dc.stop_container", return_value=True), \
         patch("core.backup_engine.dc.start_container", return_value=True):
        from core.backup_engine import run_backup
        records = run_backup(c, BackupOptions(backup_data=True, backup_compose=False))
    backup_name = records[0].filename

    # Corrupt both
    cfg_file.write_text("key: WRONG")
    (data_dir / "db.bin").write_bytes(b"WRONG")

    with patch("core.restore_engine.get_config", return_value=cfg), \
         patch("core.restore_engine._take_safety_snapshot", return_value=[]), \
         patch("core.restore_engine.dc.stop_container", return_value=True), \
         patch("core.restore_engine.dc.start_container", return_value=True):
        from core.restore_engine import run_restore
        result = run_restore(c, RestoreOptions(
            restore_data=True, restore_compose=False, backup_id_data=backup_name,
        ))

    assert result is True
    assert cfg_file.read_text() == "key: original"
    assert (data_dir / "db.bin").read_bytes() == b"DB"


def test_missing_source_skipped_with_warning_but_backup_proceeds(tmp_path):
    """A non-visible source path warns and skips; visible sources still archive."""
    good = tmp_path / "gooddata"
    good.mkdir()
    (good / "keep.txt").write_text("keep me")

    sources = [
        DataSource(host_path=str(tmp_path / "not_mounted"), destination="/gone",
                   method="bind", kind="dir"),
        DataSource(host_path=str(good), destination="/data", method="bind", kind="dir"),
    ]
    c = _container(tmp_path, sources)
    cfg = _cfg(tmp_path)
    logs = []

    with patch("core.backup_engine.get_config", return_value=cfg), \
         patch("core.backup_engine.dc.stop_container", return_value=True), \
         patch("core.backup_engine.dc.start_container", return_value=True):
        from core.backup_engine import run_backup
        records = run_backup(
            c, BackupOptions(backup_data=True, backup_compose=False),
            log_cb=lambda m, l="info": logs.append((m, l)),
        )

    assert len(records) == 1  # archive still created from the good source
    assert any("not visible" in m and l == "warning" for m, l in logs)
