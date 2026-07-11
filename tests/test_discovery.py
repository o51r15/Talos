"""
Tests for data-source discovery in core/docker_client.py.
Covers the critical logic that makes the tool work on real setups without
renaming folders: bind mounts are authoritative, name-match is a fallback,
and plumbing mounts (socket, localtime, etc.) are filtered out.
"""

import pytest
from core.models import MountInfo
from core.docker_client import (
    _discover_data_sources,
    _is_noise_bind,
    _find_data_dir_by_name,
)


class TestNoiseBindFilter:

    @pytest.mark.parametrize("path", [
        "/etc/localtime",
        "/etc/resolv.conf",
        "/etc/hostname",
        "/etc/hosts",
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/sys/fs/cgroup",
        "/proc/sys",
        "/dev/null",
        "",
    ])
    def test_noise_paths_filtered(self, path):
        assert _is_noise_bind(path) is True

    @pytest.mark.parametrize("path", [
        "/home/user/docker/grocy/data",
        "/opt/appdata/nextcloud",
        "/mnt/tank/media",
    ])
    def test_real_data_paths_kept(self, path):
        assert _is_noise_bind(path) is False

    def test_docker_sock_anywhere_filtered(self):
        assert _is_noise_bind("/custom/path/docker.sock") is True


class TestDiscoverDataSources:

    def test_bind_mount_is_primary_source(self, tmp_path):
        real = tmp_path / "appdata"
        real.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source=str(real), destination="/data"),
        ]
        sources = _discover_data_sources("some-container-name", mounts, str(tmp_path))
        assert len(sources) == 1
        assert sources[0].host_path == str(real)
        assert sources[0].destination == "/data"
        assert sources[0].method == "bind"

    def test_multiple_bind_mounts_all_captured(self, tmp_path):
        d1 = tmp_path / "config"; d1.mkdir()
        d2 = tmp_path / "data"; d2.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source=str(d1), destination="/config"),
            MountInfo(mount_type="bind", source=str(d2), destination="/data"),
        ]
        sources = _discover_data_sources("x", mounts, str(tmp_path))
        paths = {s.host_path for s in sources}
        assert paths == {str(d1), str(d2)}

    def test_noise_mounts_excluded(self, tmp_path):
        real = tmp_path / "appdata"; real.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source="/etc/localtime", destination="/etc/localtime"),
            MountInfo(mount_type="bind", source="/var/run/docker.sock", destination="/var/run/docker.sock"),
            MountInfo(mount_type="bind", source=str(real), destination="/data"),
        ]
        sources = _discover_data_sources("x", mounts, str(tmp_path))
        assert len(sources) == 1
        assert sources[0].host_path == str(real)

    def test_nonexistent_bind_source_is_kept_not_dropped(self, tmp_path):
        """
        A bind mount whose host path isn't visible (e.g. tool runs in a
        container without that path mounted) must be KEPT so preview/backup
        can warn loudly. Silently dropping it hides real data from backups.
        """
        missing = str(tmp_path / "gone")
        mounts = [
            MountInfo(mount_type="bind", source=missing, destination="/data"),
        ]
        sources = _discover_data_sources("x", mounts, str(tmp_path))
        assert len(sources) == 1
        assert sources[0].host_path == missing

    def test_file_bind_mount_captured(self, tmp_path):
        """Single-file bind mounts (configs, certs) are real data too."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("key: value")
        mounts = [
            MountInfo(mount_type="bind", source=str(cfg_file), destination="/app/config.yml"),
        ]
        sources = _discover_data_sources("x", mounts, str(tmp_path))
        assert len(sources) == 1
        assert sources[0].kind == "file"
        assert sources[0].host_path == str(cfg_file)

    def test_manual_extra_sources_added(self, tmp_path):
        """config extra_data_sources paths are appended with method=manual."""
        extra = tmp_path / "extra_stuff"
        extra.mkdir()
        real = tmp_path / "appdata"
        real.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source=str(real), destination="/data"),
        ]
        sources = _discover_data_sources(
            "x", mounts, str(tmp_path), extra_paths=[str(extra)]
        )
        assert len(sources) == 2
        methods = {s.method for s in sources}
        assert methods == {"bind", "manual"}

    def test_manual_sources_deduped_against_binds(self, tmp_path):
        real = tmp_path / "appdata"
        real.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source=str(real), destination="/data"),
        ]
        sources = _discover_data_sources(
            "x", mounts, str(tmp_path), extra_paths=[str(real)]
        )
        assert len(sources) == 1
        assert sources[0].method == "bind"

    def test_volume_mounts_are_not_data_sources(self, tmp_path):
        """Named volumes are handled by internal backup, not data sources."""
        mounts = [
            MountInfo(mount_type="volume", source="/var/lib/docker/volumes/x/_data",
                      destination="/data", name="myvol"),
        ]
        sources = _discover_data_sources("x", mounts, str(tmp_path))
        assert sources == []

    def test_name_match_fallback_when_no_bind(self, tmp_path):
        """A container named like a folder under base_data_dir uses that folder."""
        base = tmp_path / "docker"
        (base / "grocy").mkdir(parents=True)
        sources = _discover_data_sources("grocy", [], str(base))
        assert len(sources) == 1
        assert sources[0].host_path == str(base / "grocy")
        assert sources[0].method == "name"

    def test_bind_takes_priority_over_name_match(self, tmp_path):
        """When a bind mount exists, the name-match fallback is NOT used."""
        base = tmp_path / "docker"
        (base / "grocy").mkdir(parents=True)
        real = tmp_path / "actual_appdata"; real.mkdir()
        mounts = [
            MountInfo(mount_type="bind", source=str(real), destination="/data"),
        ]
        sources = _discover_data_sources("grocy", mounts, str(base))
        assert len(sources) == 1
        assert sources[0].host_path == str(real)   # bind, not the name-matched folder
        assert sources[0].method == "bind"

    def test_no_sources_when_nothing_matches(self, tmp_path):
        """The real-world 'complex setup' case degrades to empty, not a crash."""
        base = tmp_path / "docker"
        base.mkdir()
        # container named 'app-service-1', folder is 'myapp' — no match, no binds
        (base / "myapp").mkdir()
        sources = _discover_data_sources("app-service-1", [], str(base))
        assert sources == []


class TestNameMatchFallback:

    def test_case_insensitive_match(self, tmp_path):
        base = tmp_path / "docker"
        (base / "MyApp").mkdir(parents=True)
        result = _find_data_dir_by_name("myapp", str(base))
        assert result == str(base / "MyApp")

    def test_no_match_returns_none(self, tmp_path):
        base = tmp_path / "docker"
        base.mkdir()
        assert _find_data_dir_by_name("nothing", str(base)) is None

    def test_missing_base_dir_returns_none(self, tmp_path):
        assert _find_data_dir_by_name("x", str(tmp_path / "does-not-exist")) is None
