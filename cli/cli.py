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
import logging
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.logging import RichHandler
from rich import box
from typing import Optional

console = Console()


def _setup_logging(verbose: bool) -> None:
    """Route core-module logging to the console. -v enables DEBUG."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True,
                              show_path=verbose, markup=False)],
    )
    # Quiet noisy third-party loggers unless we're in verbose mode
    if not verbose:
        logging.getLogger("docker").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)


# ── CLI root ───────────────────────────────────────────────────────────────────

@click.group()
@click.option("--config", default=None, help="Path to config.yaml")
@click.option("--verbose", "-v", is_flag=True, help="Verbose (DEBUG) logging")
@click.pass_context
def cli(ctx: click.Context, config: Optional[str], verbose: bool):
    """Docker Backup Manager — CLI"""
    from core.config import load_config
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    cfg = load_config(config)
    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose


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
    table.add_column("Data Sources")

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

        # Summarise data sources and how they were found
        if c.data_sources:
            methods = {s.method for s in c.data_sources}
            n = len(c.data_sources)
            via = "/".join(sorted(methods))
            data_col = f"{n} ({via})"
        elif c.has_internal_volumes:
            data_col = "[dim]volumes only[/dim]"
        else:
            data_col = "[yellow]none[/yellow]"

        table.add_row(
            f"{c.name}{self_flag}",
            status_style,
            project or "-",
            str(len(records)),
            last_backup,
            data_col,
        )

    console.print(table)


# ── backup ─────────────────────────────────────────────────────────────────────

@cli.command("backup")
@click.option("--container", "-c", default=None, help="Container name (omit for all)")
@click.option("--data/--no-data", default=True, help="Backup data directory")
@click.option("--compose/--no-compose", default=True, help="Backup compose files")
@click.option("--internal/--no-internal", default=False, help="Backup internal volumes")
@click.option("--dry-run", is_flag=True,
              help="Show what WOULD be backed up without stopping or writing anything")
@click.option("--siblings/--no-siblings", default=False,
              help="Include compose siblings without prompting (useful for scripts)")
@click.pass_context
def backup_cmd(ctx, container, data, compose, internal, dry_run, siblings):
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
        include_compose_siblings=siblings,
    )

    if container:
        targets = [dc.get_container(container)]
        if not targets[0]:
            console.print(f"[red]Container '{container}' not found[/red]")
            sys.exit(1)
    else:
        targets = dc.list_containers(all_containers=True)

    targets = enrich_with_compose(targets)

    # ── DRY RUN ────────────────────────────────────────────────────────────────
    if dry_run:
        from core.preview import preview_backup
        console.print(Panel(
            "[bold yellow]DRY RUN[/bold yellow] — no containers will be stopped, "
            "no files will be written.",
            expand=False,
        ))
        for c in targets:
            if c.is_self:
                console.print(f"[dim]Would skip self: {c.name}[/dim]")
                continue
            plan = preview_backup(c, options)
            _render_preview(plan)
        return

    def _log_cb(msg: str, level: str = "info") -> None:
        style = {"warning": "yellow", "error": "red"}.get(level, "dim")
        console.print(f"  [{style}]{msg}[/{style}]")

    for c in targets:
        if c.is_self:
            console.print(f"[dim]Skipping self: {c.name}[/dim]")
            continue

        # Per-container options copy so group choice doesn't leak between containers
        container_opts = options.model_copy()

        # Compose-group prompt — skipped when --siblings/--no-siblings was explicit
        if (
            options.backup_compose
            and c.compose
            and c.compose.shared_containers
            and not siblings
        ):
            sibling_names = c.compose.shared_containers
            console.print(
                Panel(
                    f"[yellow]'{c.name}'[/yellow] shares a compose stack with "
                    f"{len(sibling_names)} other container(s): {', '.join(sibling_names)}",
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
            container_opts = options.model_copy(
                update={"include_compose_siblings": choice == "all"}
            )

        console.rule(f"[bold]{c.name}[/bold]")

        try:
            records = run_backup(c, container_opts, log_cb=_log_cb)
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
        console.print("\n[bold green]✓ Restore complete[/bold green]")
    else:
        console.print("\n[bold red]✗ Restore failed — check output above[/bold red]")
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


# ── inspect ────────────────────────────────────────────────────────────────────

@cli.command("inspect")
@click.argument("container")
@click.option("--internal/--no-internal", default=True,
              help="Include named-volume detection in the report")
@click.pass_context
def inspect_cmd(ctx, container, internal):
    """Show how the tool sees a container: data sources, compose, volumes."""
    from core import docker_client as dc
    from core.compose_discovery import enrich_with_compose
    from core.models import BackupOptions
    from core.preview import preview_backup

    c = dc.get_container(container)
    if not c:
        console.print(f"[red]Container '{container}' not found[/red]")
        sys.exit(1)
    c = enrich_with_compose([c])[0]

    options = BackupOptions(backup_data=True, backup_compose=True, backup_internal=internal)
    plan = preview_backup(c, options)
    _render_preview(plan)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _render_preview(plan: dict) -> None:
    """Render a preview_backup() plan dict to the console."""
    stop_note = "[yellow]will STOP then restart[/yellow]" if plan["will_stop"] else "[dim]no stop needed[/dim]"
    console.rule(f"[bold]{plan['container']}[/bold]  [dim]({plan['status']})[/dim]  {stop_note}")

    # Data sources
    data = plan["data"]
    if data["enabled"]:
        if data["sources"]:
            table = Table(title="Data sources", box=box.SIMPLE, show_header=True,
                          header_style="bold cyan")
            table.add_column("Host path", style="white")
            table.add_column("→ In container")
            table.add_column("Found via")
            table.add_column("Kind")
            table.add_column("Files", justify="right")
            table.add_column("Size", justify="right")
            for s in data["sources"]:
                exists = "" if s["exists"] else " [red](missing)[/red]"
                table.add_row(
                    f"{s['host_path']}{exists}",
                    s["destination"] or "[dim]—[/dim]",
                    s["method"],
                    s.get("kind", "dir"),
                    str(s["files"]),
                    s["human"],
                )
            console.print(table)
            console.print(f"  [dim]Total data: {data.get('total_human', '0 B')}[/dim]")
        else:
            console.print("  [yellow]Data: no sources found — would be skipped[/yellow]")
    else:
        console.print("  [dim]Data backup disabled[/dim]")

    # Compose
    comp = plan["compose"]
    if comp["enabled"]:
        if comp["files"]:
            method = comp["method"]
            console.print(f"  [cyan]Compose[/cyan] (via {method}, working_dir: {comp['working_dir'] or '—'}):")
            for f in comp["files"]:
                miss = "" if f["exists"] else " [red](missing)[/red]"
                console.print(f"    • {f['path']}{miss}")
        else:
            console.print("  [yellow]Compose: nothing discovered — would be skipped[/yellow]")
    else:
        console.print("  [dim]Compose backup disabled[/dim]")

    # Internal
    intern = plan["internal"]
    if intern["enabled"]:
        if intern["volumes"]:
            console.print("  [cyan]Named volumes:[/cyan]")
            for v in intern["volumes"]:
                console.print(f"    • {v['name']} → {v['destination']}")
        else:
            console.print("  [yellow]Internal: no named volumes — would be skipped[/yellow]")

    # Siblings
    if plan["siblings"]:
        console.print(f"  [cyan]Compose siblings to include:[/cyan] {', '.join(plan['siblings'])}")

    # Destination
    console.print(f"  [dim]Archives would be written to: {plan['dest_dir']}[/dim]")

    # Warnings
    if plan["warnings"]:
        console.print()
        for w in plan["warnings"]:
            console.print(f"  [yellow]⚠ {w}[/yellow]")
    console.print()


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
