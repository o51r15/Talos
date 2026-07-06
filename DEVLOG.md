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
- [x] Compose siblings backup (include_compose_siblings full implementation)
- [x] Env var override for config values
- [x] Fix retention.py Optional import location
- [x] Volume backup using named volume approach (backup_named_volume wiring)
- [x] Scheduled backups (APScheduler, config-driven, lifespan-managed)
- [x] Config API endpoint (GET /api/config, reload, schedule status)
- [x] Restore engine: named volume restore (timestamp-grouped, per-volume busybox)
- [x] Testing (unit — 75 tests, pyflakes clean, all modules import)
- [x] README: updated with schedule config, env vars, test docs
- [x] Git repo + GHCR build workflow on push
- [x] Two full top-down code review passes (static + suite-run)
- [ ] Wire pytest into the GitHub Actions build (gate image on green tests)
- [ ] First real-world test run on Linux host
- [ ] Auth implementation (phase 2)
- [ ] Persist job history across restarts (currently in-memory only)
- [ ] Docker client auto-reconnect if daemon restarts

---

## Change Log

| Date | Note |
|------|------|
| 2026-07-06 | Brainstorm session complete, all core decisions locked, dev log created |
| 2026-07-06 | Phase 1 scaffold complete — all modules written, web UI built, CLI functional |
| 2026-07-06 | Phase 2 — bug fixes, compose siblings, named volume backup, env var overrides, scheduler, config API |
| 2026-07-06 | Phase 3 — restore engine rewrite (timestamp-grouped volume restore), 73-test suite, JS snapshot grouping, README rewrite |
| 2026-07-06 | Git — repo pushed to github.com/o51r15/Talos; GHCR dev-container build workflow on push (amd64+arm64) |
| 2026-07-06 | Review pass 1 (static) — :ro data mount, retention .name bug, CLI options mutation, datetime.utcnow deprecation, .dockerignore, dead code |
| 2026-07-06 | Review pass 2 (ran suite+pyflakes) — data-restore wrong-dir bug, snapshot not restorable, list_jobs tz crash, cron crash, tarfile hardening; 75 tests green |

---

## Phase 3 — Restore Rewrite + Test Suite — 2026-07-06

### Restore engine
- `_restore_internal` rewritten: client passes any one file from a snapshot
  set; engine finds ALL internal files sharing that timestamp and restores
  each named volume via busybox. Legacy docker-save `.tar` files still handled.
- Fixed `_take_safety_snapshot` calling the removed `_backup_internal`
  (renamed to `_backup_named_volumes` in Phase 2).

### Test suite (tests/)
- 75 tests, no Docker daemon needed — all SDK calls mocked.
- Coverage: models (filename parsing), config (YAML + env overrides),
  retention (keep_last/max_age/type-independence/snapshot-exclusion),
  backup_engine (data/compose/volumes/lifecycle/siblings/recursion-guard),
  restore_engine (snapshot-first/abort/data-clear/volume-restore/purge).
- `pytest.ini`, `requirements-dev.txt` added.

### Web UI
- Restore modal groups internal backups by timestamp into snapshot sets
  (one radio per set, shows N volumes + names + total size).
- Per-volume rows show `vol: {name}` badge.

---

## Review Pass 1 — Static Audit — 2026-07-06

Read every file top-down. Found and fixed:
- **docker-compose.yml**: `/docker` mounted `:ro` — broke every restore
  (restore writes to base_data_dir). Changed to `:rw`.
- **retention.py**: snapshot-dir exclusion used `.name` comparison — a
  container literally named `restore_snapshot` would have its backups
  silently skipped forever. Switched to `Path.resolve()`.
- **cli.py**: `include_compose_siblings` mutated on the shared options object
  inside the backup loop — a group choice leaked into every later container.
  Now a per-container `model_copy()`.
- **models.py / jobs.py**: `datetime.utcnow()` deprecated in 3.12; replaced
  with `datetime.now(timezone.utc)`.
- **Dockerfile**: dropped `docker.io` apt install (~200MB) — SDK uses the
  socket, never the CLI binary.
- Dead code: unused `shutil`, `get_latest_backup`, `_LABEL_SERVICE`,
  `Callable`; redundant `stat()`; `_log_cb` redefined per loop iteration.
- Added `.dockerignore` (was shipping tests/DEVLOG/.github into the image).

---

## Review Pass 2 — Ran the Suite — 2026-07-06

This pass installed deps and actually ran pytest + pyflakes. That surfaced
bugs static reading missed:

**Critical:**
- **restore_engine._restore_data**: extracted into `data_dir.parent` while
  stripping the archive's top dir — renamed data dirs meant files landed in
  the wrong place. Now extracts INTO `data_dir`. Regression test asserts
  files land inside data_dir and NOT the parent.
- **restore_engine**: backup IDs resolved only against `backup_dest`, so
  safety snapshots the UI listed were NOT actually restorable. Added
  `_resolve_backup_path()` checking both dirs; all three restore paths use it.
- **jobs.list_jobs**: naive `datetime.min` fallback sorted against tz-aware
  `started_at` → TypeError whenever a job lacked a start time. Fallback now
  tz-aware.
- **scheduler.start_scheduler**: invalid cron raised inside FastAPI lifespan
  and killed web startup. Now caught + logged; server starts scheduler-off.

**Hardening:**
- tarfile extraction uses `filter='data'` on 3.12+ (blocks path traversal).
- compose `working_dir` prefix match is now separator-terminated
  (`/docker/app` no longer matches `/docker/app2`).
- restore API rejects a type enabled with no backup selected (was a silent
  no-op reporting success). Returns 400.
- app.js: restore type enabled only if checked AND a backup selected;
  surfaces FastAPI 400 detail; snapshots now offered in the picker.

**Dead code (pyflakes):** unused `Path`/`Any` in compose_discovery, duplicated
local `import os` in docker_client, two placeholder-less f-strings in cli.

**Tests:** fixed 6 pre-existing test bugs only visible when run (wrong patch
target for a local import; callback signatures missing level default).
**Final: 75 passed, pyflakes clean, all modules import.**

### Verified tooling state
- Local env is Python 3.13; project targets 3.12+ (tarfile data filter guarded).
- `rich` not installed locally so `cli.cli` import is unverified locally, but
  all non-CLI modules import clean and the CLI is pure Click/Rich glue.
