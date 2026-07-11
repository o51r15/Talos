"""
Shared pytest fixtures.
"""

import pytest


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """
    Reset the config singleton before and after every test.
    Without this, a test that loads config bleeds into the next test.
    """
    import core.config as cfg_mod
    cfg_mod._config = None
    cfg_mod._config_path = None
    yield
    cfg_mod._config = None
    cfg_mod._config_path = None


@pytest.fixture
def minimal_config(tmp_path):
    """Write a minimal config.yaml in tmp_path and return the path."""
    p = tmp_path / "config.yaml"
    p.write_text(
        f"backup_dest: {tmp_path}/backups\n"
        f"restore_snapshot_dir: {tmp_path}/snapshots\n"
        f"base_data_dir: {tmp_path}/docker\n"
    )
    return str(p)


@pytest.fixture
def running_container(tmp_path):
    """A ContainerInfo representing a running container with a bind-mount data source."""
    from core.models import ContainerInfo, ContainerStatus, DataSource, MountInfo

    data_dir = tmp_path / "docker" / "myapp"
    data_dir.mkdir(parents=True)
    (data_dir / "app.db").write_bytes(b"x" * 4096)
    (data_dir / "config.json").write_bytes(b"{}")

    return ContainerInfo(
        id="abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        short_id="abc123def4",
        name="myapp",
        status=ContainerStatus.RUNNING,
        image="myapp:latest",
        data_dir=str(data_dir),
        data_sources=[
            DataSource(host_path=str(data_dir), destination="/data", method="bind"),
        ],
        mounts=[
            MountInfo(mount_type="bind", source=str(data_dir), destination="/data"),
        ],
        has_external_mounts=True,
        has_internal_volumes=False,
    )


@pytest.fixture
def stopped_container(running_container):
    """Same container but stopped."""
    from core.models import ContainerStatus
    running_container.status = ContainerStatus.STOPPED
    return running_container
