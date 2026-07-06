# Docker Backup Manager — Development Log

## Project Overview
A Python/FastAPI tool to manage Docker container backups and restores.
Runs as a standalone CLI script or as a Docker container with a web UI.

---

## Brainstorm Session — 2026-07-06

### Core Decisions Locked

**Runtime**
- Python + FastAPI
- Dual mode: CLI (all containers, automated) and Web UI (per-container, interactive)
- Deployable as a standalone script or Docker container
- When running as a container, skips itself — identified via env variable or config option

**Config**
- YAML format
- Drives: backup path, restore snapshot path, retention policy, post-restore snapshot purge behavior, self-identification

**Backup**
- Source: physical data folders mapped to containers (bidirectional reconciliation)
- Format: tar.gz, named `<container>_backup_<timestamp>.tar.gz`
- Compose files: separate tar, named `<container>_compose_<timestamp>.tar.gz`
- Internal (non-mounted) container data: `docker commit` + `docker save` for accuracy over speed
- Compose discovery: via `docker inspect` labels (`com.docker.compose.project.working_dir`,
  `com.docker.compose.project.config_files`), fallback to configurable scan path,
  graceful unknown state if neither works

**Compose-Aware Grouping**
- Containers sharing a compose stack are grouped
- On backup: prompt — backup all / single / cancel
- On restore: warn that other containers in the stack are affected
- Applies to both CLI prompts and Web UI dialogs

**Storage**
- Path-based only — no NFS/SMB awareness, handled at OS level
- Config points to a directory; what backs it is irrelevant to the script

**Restore**
- Safety snapshot taken before any restore, saved to `/Backups/restore_snapshot/` (configurable)
- Snapshot retention after successful restore: configurable (keep or purge)
- Restore is selectable from backup history (multiple backups per container)
- Partial restore supported: data only / compose only / both, independently selectable
- Full restore sequence: safety snapshot → stop container → delete current state → restore from selected backup → restart

**Web UI**
- Rich interface, not minimal
- Container list with live status
- Per-container: backup history, manual backup trigger, restore selection
- Compose group awareness surfaced in UI
- Auth: hooks designed in from start, disabled by default, config-toggled later
- Log viewer: deferred, not in initial build

**Existing Script Notes (docker_backup.sh)**
- Folder-first iteration — needs to become bidirectional (container↔folder)
- Grep name match is fragile — replace with inspect label mapping
- Stop → backup → restart sequence is sound, carries forward
- Cleanup is global mtime wipe — replace with per-container retention from config
- No compose, no internal data, no restore — all new

---

## Phase 1 — Initial Scaffold — 2026-07-06

### Project Structure Created

```
docker-backup-manager/
├── main.py                     # Entry point (CLI or web)
├── config.yaml                 # Default configuration
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── DEVLOG.md
├── core/
│   ├── models.py               # Pydantic data models
│   ├── config.py               # YAML config loader (singleton)
│   ├── docker_client.py        # Docker SDK wrapper
│   ├── compose_discovery.py    # Compose file finder (labels → scan fallback)
│   ├── backup_engine.py        # Backup orchestration (data + compose + internal)
│   ├── restore_engine.py       # Restore + safety snapshot
│   ├── retention.py            # Cleanup per retention policy
│   ├── backup_index.py         # Scan backup dir, return BackupRecord lists
│   └── jobs.py                 # In-memory job tracker + thread pool runner
├── api/
│   ├── app.py                  # FastAPI app factory
│   ├── middleware/
│   │   └── auth.py             # Auth middleware stub (pass-through when disabled)
│   └── routes/
│       ├── containers.py       # GET /api/containers[/{name}]
│       ├── backups.py          # GET+POST /api/backups[/{container}]
│       ├── restore.py          # GET+POST /api/restore[/{container}]
│       └── jobs.py             # GET /api/jobs[/{id}]
├── cli/
│   └── cli.py                  # Click CLI: list / backup / restore / web
└── web/
    ├── templates/index.html    # Single-page app shell
    └── static/
        ├── css/main.css        # Dark ops dashboard styling
        └── js/app.js           # Vanilla JS SPA logic
```

### Key Design Notes

**Backup filename format:**
`{container_name}_{type}_{YYYY-MM-DD_HH-MM-SS}.tar.gz`
Types: `data`, `compose`, `internal` (internal uses `.tar` not `.tar.gz`)

**Backup directory structure:**
```
{backup_dest}/
  {container_name}/
    container_data_2026-07-06_00-00-00.tar.gz
    container_compose_2026-07-06_00-00-00.tar.gz
{restore_snapshot_dir}/
  {container_name}/
    container_data_2026-07-06_10-30-00.tar.gz
```

**Internal data method:** `docker commit` → `docker save` (image tar).
Chosen for accuracy and stability over speed. Named volumes use busybox
helper container approach.

**Job system:** ThreadPoolExecutor (max 4 workers). API returns job immediately,
client polls `/api/jobs/{id}` every 2s for status and log tail.

**Web UI design:** Dark ops aesthetic. JetBrains Mono for data/IDs, Inter for
labels. Pulsing LED indicators for running containers. Two-panel layout:
container list sidebar + detail panel. Modals for backup/restore configuration.

### Known Issues / Next Steps

- [ ] `retention.py` has an `Optional` import at the bottom to avoid circular —
      should be cleaned up by moving to `from __future__ import annotations`
- [ ] `docker_client.py` `backup_named_volume()` — volume name extraction from
      dest_path is fragile (rsplit). Should pass directory explicitly.
- [ ] Compose siblings backup (include_compose_siblings) — the flag is stored in
      options but backup_engine doesn't yet iterate siblings. Needs implementation.
- [ ] Self-identification: env var `SELF_CONTAINER_NAME` is set in docker-compose
      but the config loader reads from YAML only — need env var override in config.py
- [ ] `web_cmd` in CLI calls uvicorn directly — should respect web_host/web_port
      from config (already does, but test this)
- [ ] Error responses from API routes should be more consistent (standardize
      error envelope)

---

## Status

- [x] Brainstorm complete
- [x] Working directory created
- [x] Project structure scaffolded
- [x] YAML config schema defined
- [x] Docker inspect / compose discovery module
- [x] Backup engine (data + compose + internal)
- [x] Restore engine (safety snapshot + partial restore)
- [x] CLI interface (list / backup / restore / web)
- [x] FastAPI backend + all routes
- [x] Web UI frontend (dark ops dashboard)
- [x] Self-exclusion logic
- [x] Retention / cleanup logic
- [x] Auth hooks (stub, disabled)
- [x] Dockerfile + docker-compose.yml
- [ ] Compose siblings backup (include_compose_siblings full implementation)
- [ ] Env var override for config values
- [ ] Testing (unit + integration)
- [ ] Fix retention.py Optional import location
- [ ] Volume backup using named volume approach (backup_named_volume wiring)
- [ ] First real-world test run

---

## Change Log

| Date | Note |
|------|------|
| 2026-07-06 | Brainstorm session complete, all core decisions locked, dev log created |
| 2026-07-06 | Phase 1 scaffold complete — all modules written, web UI built, CLI functional |
