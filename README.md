# Talos — Docker Backup Manager

A Python/FastAPI tool for managing Docker container backups and restores.
Runs as a CLI script or as a self-contained Docker container with a web UI.

## Features

- **Backup** data directories, compose files, and named volumes per container
- **Restore** from any historical backup with a selectable backup list
- **Safety snapshot** taken automatically before every restore
- **Compose-aware** — detects shared stacks, prompts for group or single backup
- **Scheduled backups** — cron-driven, config or env var controlled
- **Rich web UI** — container status, backup history, one-click backup/restore
- **CLI mode** — fully scriptable, drop-in replacement for shell backup scripts
- **Retention policy** — keep last N, purge by age, per-container, YAML-driven

## Quick Start

### CLI (standalone)

```bash
pip install -r requirements.txt
python main.py list
python main.py backup
python main.py backup --container myapp
python main.py restore myapp
python main.py web
```

### Docker / Compose

```bash
# Edit config.yaml and docker-compose.yml to match your environment
docker compose up -d
# Web UI: http://localhost:8000
# API docs: http://localhost:8000/api/docs
```

## Configuration

All settings live in `config.yaml`. Every key can also be overridden by an
environment variable (env vars take precedence over the file).

### Full config reference

```yaml
# Storage
backup_dest: /backups
restore_snapshot_dir: /backups/restore_snapshot
base_data_dir: /docker            # root where container data folders live
compose_scan_paths:               # fallback paths to scan for compose files
  - /docker
  - /opt/docker
  - /home

# Retention
retention:
  keep_last: 7                    # keep N most recent per container per type
  max_age_days: 30                # delete anything older (0 = disabled)

# Restore
purge_snapshot_on_success: false  # delete safety snapshot after good restore

# Self-identification (skip this container during backup/restore)
self_container_name: ""           # set to this container's name

# Web server
web_host: "0.0.0.0"
web_port: 8000

# Scheduled backups
schedule:
  enabled: false
  cron: "0 2 * * *"              # standard cron, runs in UTC
  backup_data: true
  backup_compose: true
  backup_internal: false          # named volume backup (slower)
  skip_stopped: true              # skip containers that aren't running

# Authentication (stub — disabled; activate in a future phase)
auth:
  enabled: false
  secret_key: "changeme"
  username: "admin"
  password_hash: ""
```

### Environment variable overrides

| Variable              | Config key                  | Type   |
|-----------------------|-----------------------------|--------|
| `CONFIG_PATH`         | —                           | string |
| `SELF_CONTAINER_NAME` | `self_container_name`       | string |
| `BACKUP_DEST`         | `backup_dest`               | string |
| `RESTORE_SNAPSHOT_DIR`| `restore_snapshot_dir`      | string |
| `BASE_DATA_DIR`       | `base_data_dir`             | string |
| `WEB_HOST`            | `web_host`                  | string |
| `WEB_PORT`            | `web_port`                  | int    |
| `SCHEDULE_ENABLED`    | `schedule.enabled`          | bool   |
| `SCHEDULE_CRON`       | `schedule.cron`             | string |

Boolean env vars accept: `true`, `1`, `yes`, `on` (case-insensitive).

## Backup Format

Archives stored at `{backup_dest}/{container_name}/`:

| Filename pattern | Contents |
|------------------|----------|
| `{name}_data_{ts}.tar.gz` | Host-mounted data directory |
| `{name}_compose_{ts}.tar.gz` | Compose file(s) + `.env` |
| `{name}_internal_vol-{volname}_{ts}.tar.gz` | One named Docker volume |

Safety snapshots use the same format, stored under `{restore_snapshot_dir}/{container_name}/`.

## Restore Sequence

1. Safety snapshot of current state → `restore_snapshot_dir`
2. Stop container
3. Wipe selected data paths
4. Extract selected backup archive(s)
5. Start container
6. Optionally purge safety snapshot (`purge_snapshot_on_success: true`)

Partial restores are supported: data only, compose only, internal only, or any combination.

Internal volume restores are grouped by timestamp — selecting any file from a snapshot
set restores all volumes captured at that point in time.

## API

When running in web mode the API is available at `/api/docs`.

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/containers` | List all containers with status |
| `GET` | `/api/backups/{name}` | Backup history for a container |
| `POST` | `/api/backups/{name}` | Trigger backup |
| `POST` | `/api/restore/{name}` | Trigger restore |
| `GET` | `/api/jobs/{id}` | Poll job status |
| `GET` | `/api/config` | Current effective config |
| `POST` | `/api/config/reload` | Reload config from disk |
| `GET` | `/api/config/schedule` | Scheduler status + next run |

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests are unit tests only — no Docker daemon required. All Docker SDK calls are mocked.

## Project Structure

```
core/           Core logic
  config.py     YAML loader + env var override layer
  docker_client.py  Docker SDK wrapper
  compose_discovery.py  Compose file finder
  backup_engine.py  Backup orchestration
  restore_engine.py  Restore + safety snapshot
  retention.py  Cleanup per retention policy
  scheduler.py  APScheduler integration
  jobs.py       In-memory job tracker
api/            FastAPI routes and middleware
cli/            Click CLI
web/            HTML/CSS/JS frontend (dark ops dashboard)
tests/          Pytest unit tests
```

## Docker group permissions

The container needs access to the Docker socket. Find your host's Docker GID:

```bash
stat -c '%g' /var/run/docker.sock
```

Update `group_add` in `docker-compose.yml` to match.
