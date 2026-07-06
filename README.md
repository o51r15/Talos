# Talos — Docker Backup Manager

A Python/FastAPI tool for managing Docker container backups and restores.
Runs as a CLI script or as a self-contained Docker container with a web UI.

## Features

- **Backup** data directories, compose files, and internal volumes per container
- **Restore** from any historical backup with a selectable backup list
- **Safety snapshot** taken automatically before every restore
- **Compose-aware** — detects shared stacks, prompts for group or single backup
- **Rich web UI** — container status, backup history, one-click backup/restore
- **CLI mode** — fully scriptable, drop-in replacement for shell backup scripts
- **Retention policy** — keep last N backups, purge by age, configured in YAML

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
# Edit config.yaml and docker-compose.yml paths to match your environment
docker compose up -d
# Web UI available at http://localhost:8000
```

## Configuration

Copy `config.yaml` and edit to match your environment. Key settings:

| Key | Default | Description |
|-----|---------|-------------|
| `backup_dest` | `/backups` | Where backup archives are stored |
| `restore_snapshot_dir` | `/backups/restore_snapshot` | Safety snapshots before restore |
| `base_data_dir` | `/docker` | Root directory containing container data folders |
| `retention.keep_last` | `7` | Number of backups to keep per container per type |
| `retention.max_age_days` | `30` | Delete backups older than N days (0 = disabled) |
| `purge_snapshot_on_success` | `false` | Delete safety snapshot after successful restore |
| `self_container_name` | `""` | Container name to skip (set to this container's name) |

## Backup Format

Backups are stored as:
```
{backup_dest}/
  {container_name}/
    {container_name}_data_{YYYY-MM-DD_HH-MM-SS}.tar.gz
    {container_name}_compose_{YYYY-MM-DD_HH-MM-SS}.tar.gz
    {container_name}_internal_{YYYY-MM-DD_HH-MM-SS}.tar
```

## Restore Sequence

1. Safety snapshot of current state → `restore_snapshot_dir`
2. Stop container
3. Wipe selected data paths
4. Extract selected backup archive
5. Restart container
6. Optionally purge safety snapshot (config: `purge_snapshot_on_success`)

## Project Structure

```
core/           Core logic (docker client, backup, restore, retention)
api/            FastAPI routes and middleware
cli/            Click CLI interface
web/            HTML/CSS/JS frontend
config.yaml     Default configuration template
Dockerfile      Container build
docker-compose.yml  Deployment example
```

## Status

Active development — Phase 1 scaffold complete. See DEVLOG.md for detailed progress.
