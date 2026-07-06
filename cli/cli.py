"""
CLI interface. Uses Click for argument parsing and Rich for output.

Commands:
  list      — show all containers and backup status
  backup    — backup one or all containers
  restore   — interactive restore for a container
  web       — start the web server
"""

from __future__ import annotations
import sys
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box
from typing import Optional

console = Console()


# ── CLI root ───────────────────────────────────────────────────────────────────

@click.group()
@click.option("--config", default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx: click.Context, config: Optional[str]):
    """Docker Backup Manager — CLI"""
    from core.config import load_config
    ctx.ensure_object(dict)
    cfg = load_config(config)
    ctx.obj["config"] = cfg


# ── list ───────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.pass_context
def list_cmd(ctx):
    """List all containers and their backup status."""
    from core import docker_client as dc
    from core.compose_discovery import enrich_with_compose
    from core.backup_index import list_backups_for_container

    containers = dc.list_containers(all_containers=True)
    containers = enrich_with_compose(containers)

    table = Table(
        title="Docker Containers",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="bold white")
    table.add_column("Status", justify="center")
    table.add_column("Compose Project")
    table.add_column("Backups", justify="right")
    table.add_column("Last Backup")
    table.add_column("Data Dir")

    for c in containers:
        status_style = {
            "running": "[green]●[/green] running",
            "stopped": "[yellow]●[/yellow] stopped",
            "exited": "[red]●[/red] exited",
            "paused": "[blue]●[/blue] paused",
        }.get(c.status.value, f"[dim]{c.status.value}[/dim]")

        records = list_backups_for_container(c.name)
        last_backup = records[0].timestamp.strftime("%Y-%m-%d %H:%M") if records else "never"
        project = c.compose.project_name if c.compose else "-"
        self_flag = " [dim](self)[/dim]" if c.is_self else ""

        table.add_row(
            f"{c.name}{self_flag}",
            status_style,
            project or "-",
            str(len(records)),
            last_backup,
            c.data_dir or "[dim]none[/dim]",
        )

    console.print(table)


# ── backup ─────────────────────────────────────────────────────────────────────

@cli.command("backup")
@click.option("--container", "-c", default=None, help="Container name (omit for all)")
@click.option("--data/--no-data", default=True, help="Backup data directory")
@click.option("--compose/--no-compose", default=True, help="Backup compose files")
@click.option("--internal/--no-internal", default=False, help="Backup internal volumes")
@click.pass_context
def backup_cmd(ctx, container, data, compose, internal):
    """Back up one or all containers."""
    from core import docker_client as dc
    from core.compose_discovery import enrich_with_compose
    from core.models import BackupOptions
    from core.backup_engine import run_backup
    from core.retention import run_retention

    options = BackupOptions(
        backup_data=data,
        backup_compose=compose,
        backup_internal=internal,
    )

    if container:
        targets = [dc.get_container(container)]
        if not targets[0]:
            console.print(f"[red]Container '{container}' not found[/red]")
            sys.exit(1)
    else:
        targets = dc.list_containers(all_containers=True)

    targets = enrich_with_compose(targets)

    for c in targets:
        if c.is_self:
            console.print(f"[dim]Skipping self: {c.name}[/dim]")
            continue

        # Compose-group prompt
        if (
            options.backup_compose
            and c.compose
            and c.compose.shared_containers
            and not options.include_compose_siblings
        ):
            siblings = c.compose.shared_containers
            console.print(
                Panel(
                    f"[yellow]'{c.name}'[/yellow] shares a compose stack with "
                    f"{len(siblings)} other container(s): {', '.join(siblings)}",
                    title="Compose Group Detected",
                )
            )
            choice = Prompt.ask(
                "Backup scope",
                choices=["all", "single", "cancel"],
                default="all",
            )
            if choice == "cancel":
                console.print(f"[dim]Skipped {c.name}[/dim]")
                continue
            options.include_compose_siblings = choice == "all"

        console.rule(f"[bold]{c.name}[/bold]")

        def _log_cb(msg: str, level: str = "info") -> None:
            style = {"warning": "yellow", "error": "red"}.get(level, "dim")
            console.print(f"  [{style}]{msg}[/{style}]")

        try:
            records = run_backup(c, options, log_cb=_log_cb)
            run_retention(c.name)
            console.print(f"  [green]✓ {len(records)} archive(s) created[/green]")
        except Exception as e:
            console.print(f"  [red]✗ Backup failed: {e}[/red]")

    console.print("\n[bold green]Done.[/bold green]")


# ── restore ────────────────────────────────────────────────────────────────────

@cli.command("restore")
@click.argument("container")
@click.pass_context
def restore_cmd(ctx, container):
    """Interactively restore a container from a backup."""
    from core import docker_client as dc
    from core.compose_discovery import enrich_with_compose
    from core.backup_index import list_backups_for_container
    from core.models import RestoreOptions
    from core.restore_engine import run_restore

    c = dc.get_container(container)
    if not c:
        console.print(f"[red]Container '{container}' not found[/red]")
        sys.exit(1)

    if c.is_self:
        console.print("[red]Cannot restore the backup manager itself[/red]")
        sys.exit(1)

    c = enrich_with_compose([c])[0]

    records = list_backups_for_container(container)
    if not records:
        console.print(f"[yellow]No backups found for '{container}'[/yellow]")
        sys.exit(0)

    # Show available backups
    console.print(Panel(f"Restore: [bold]{container}[/bold]", expand=False))

    data_records = [r for r in records if r.backup_type.value == "data"]
    compose_records = [r for r in records if r.backup_type.value == "compose"]
    internal_records = [r for r in records if r.backup_type.value == "internal"]

    options = RestoreOptions()

    # Select data backup
    if data_records:
        options.restore_data = Confirm.ask("Restore data directory?", default=True)
        if options.restore_data:
            _show_backup_list("Data backups", data_records)
            idx = Prompt.ask("Select backup number", default="1")
            try:
                options.backup_id_data = data_records[int(idx) - 1].filename
            except (IndexError, ValueError):
                console.print("[red]Invalid selection[/red]")
                sys.exit(1)

    # Select compose backup
    if compose_records:
        options.restore_compose = Confirm.ask("Restore compose file(s)?", default=True)
        if options.restore_compose:
            _show_backup_list("Compose backups", compose_records)
            idx = Prompt.ask("Select backup number", default="1")
            try:
                options.backup_id_compose = compose_records[int(idx) - 1].filename
            except (IndexError, ValueError):
                console.print("[red]Invalid selection[/red]")
                sys.exit(1)

    # Select internal backup
    if internal_records:
        options.restore_internal = Confirm.ask("Restore internal container data?", default=False)
        if options.restore_internal:
            _show_backup_list("Internal backups", internal_records)
            idx = Prompt.ask("Select backup number", default="1")
            try:
                options.backup_id_internal = internal_records[int(idx) - 1].filename
            except (IndexError, ValueError):
                console.print("[red]Invalid selection[/red]")
                sys.exit(1)

    # Confirm
    console.print(Panel(
        "[yellow]A safety snapshot will be taken before restore begins.\n"
        "The current state will be preserved in the restore_snapshot directory.[/yellow]",
        title="⚠  Warning",
    ))

    if not Confirm.ask(f"Proceed with restore of '{container}'?", default=False):
        console.print("[dim]Restore cancelled[/dim]")
        sys.exit(0)

    def _log_cb(msg: str, level: str = "info") -> None:
        style = {"warning": "yellow", "error": "red"}.get(level, "dim")
        console.print(f"  [{style}]{msg}[/{style}]")

    success = run_restore(c, options, log_cb=_log_cb)
    if success:
        console.print(f"\n[bold green]✓ Restore complete[/bold green]")
    else:
        console.print(f"\n[bold red]✗ Restore failed — check output above[/bold red]")
        sys.exit(1)


# ── web ────────────────────────────────────────────────────────────────────────

@cli.command("web")
@click.pass_context
def web_cmd(ctx):
    """Start the web UI server."""
    import uvicorn
    from core.config import get_config
    from api.app import create_app

    cfg = get_config()
    app = create_app()
    console.print(
        Panel(
            f"[bold green]Docker Backup Manager[/bold green]\n"
            f"Web UI: http://{cfg.web_host}:{cfg.web_port}\n"
            f"API docs: http://{cfg.web_host}:{cfg.web_port}/api/docs",
            title="Starting Web Server",
        )
    )
    uvicorn.run(app, host=cfg.web_host, port=cfg.web_port)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _show_backup_list(title: str, records) -> None:
    table = Table(title=title, box=box.SIMPLE, show_header=True)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Date")
    table.add_column("Size", justify="right")
    table.add_column("Filename")
    for i, r in enumerate(records, 1):
        table.add_row(
            str(i),
            r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            r.size_human,
            r.filename,
        )
    console.print(table)
