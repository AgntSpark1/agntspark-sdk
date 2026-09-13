"""
AgntSpark CLI — command-line interface for the SDK.

Install with::

    pip install agntspark

Available commands::

    agntspark init                     # Create ~/.agntspark/config.yaml
    agntspark deploy <path>            # Deploy an agent from a config file
    agntspark scale <agent-id> up|down # Manually scale an agent
    agntspark delete <agent-id>        # Permanently delete an agent
    agntspark list [--status running]  # List agents
    agntspark logs <agent-id>          # Tail agent logs
    agntspark metrics <agent-id>       # Show agent metrics
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .client import Client
from .config import Config
from .exceptions import AgntSparkError
from .models import AgentStatus, DeployConfig

console = Console()


def _get_client(ctx: click.Context) -> Client:
    """Load a Client from the click context or create one from config."""
    if ctx.obj and isinstance(ctx.obj, Client):
        return ctx.obj
    return Client()


# ===========================================================================
# CLI group
# ===========================================================================


@click.group()
@click.option("--api-key", envvar="AGNTSPARK_API_KEY", help="AgntSpark API key.")
@click.option("--base-url", envvar="AGNTSPARK_BASE_URL", help="API base URL.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
@click.pass_context
def main(ctx: click.Context, api_key: str | None, base_url: str | None, verbose: bool) -> None:
    """
    AgntSpark CLI — manage AI agents from the terminal.

    Run ``agntspark init`` first to configure your API key, or set
    ``AGNTSPARK_API_KEY`` as an environment variable.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose

    if api_key or base_url:
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        try:
            ctx.obj = Client(**kwargs)
        except AgntSparkError:
            if not ctx.invoked_subcommand == "init":
                raise
    else:
        try:
            ctx.obj = Client()
        except AgntSparkError:
            pass  # Will be handled per-command; `init` doesn't need a client.


# ===========================================================================
# init
# ===========================================================================


@main.command()
@click.option(
    "--api-key",
    prompt="AgntSpark API Key",
    hide_input=True,
    help="Your AgntSpark API key.",
)
@click.option(
    "--base-url",
    default="https://api.agntspark.com/v1",
    prompt="API Base URL",
    help="API base URL.",
)
def init(api_key: str, base_url: str) -> None:
    """Create or update ~/.agntspark/config.yaml."""
    config = Config(api_key=api_key, base_url=base_url)
    saved_path = config.save()
    console.print(
        Panel.fit(
            f"[green]✓[/green] Configuration saved to [cyan]{saved_path}[/cyan]\n"
            f"  API Key: {'•' * 8}{api_key[-4:]}\n"
            f"  Base URL: {base_url}",
            title="AgntSpark Config",
            border_style="green",
        )
    )


# ===========================================================================
# deploy
# ===========================================================================


@main.command()
@click.argument("config_file", type=click.Path(exists=True, path_type=Path))
@click.option("--name", "-n", help="Override agent name from config file.")
@click.option("--wait", "-w", is_flag=True, help="Wait for deployment to complete.")
@click.pass_context
def deploy(ctx: click.Context, config_file: Path, name: str | None, wait: bool) -> None:
    """Deploy an agent from a YAML/JSON configuration file."""
    with open(config_file) as f:
        if config_file.suffix in (".yaml", ".yml"):
            cfg_data = yaml.safe_load(f)
        else:
            cfg_data = json.load(f)

    if name:
        cfg_data["name"] = name

    client = _get_client(ctx)

    with console.status("[bold blue]Creating agent…"):
        agent = client.agents.create(
            name=cfg_data["name"],
            runtime=cfg_data.get("runtime"),
            framework=cfg_data.get("framework", "custom"),
            model=cfg_data.get("model", "gpt-4o"),
            system_prompt=cfg_data.get("system_prompt"),
            tags=cfg_data.get("tags", []),
            metadata=cfg_data.get("metadata", {}),
        )

    console.print(f"[green]✓[/green] Agent created: [cyan]{agent.id}[/cyan]")

    # Build deploy config if present
    deploy_cfg = None
    if "deploy" in cfg_data:
        deploy_cfg = DeployConfig(**cfg_data["deploy"])

    with console.status("[bold blue]Deploying agent…"):
        deployed = client.agents.deploy(agent.id, config=deploy_cfg)

    table = Table(title="Deployment Summary", box=box.ROUNDED)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Agent ID", deployed.id)
    table.add_row("Name", deployed.name)
    table.add_row("Status", _status_colored(deployed.status))
    table.add_row("Replicas", str(deployed.replicas))
    table.add_row("URL", str(deployed.url) if deployed.url else "—")
    console.print(table)

    if wait:
        console.print("[dim]Waiting for agent to reach running state…[/dim]")
        import time

        for _ in range(60):
            time.sleep(2)
            current = client.agents.get(agent.id)
            if current.status == AgentStatus.RUNNING:
                console.print(f"[green]✓[/green] Agent is running at {current.url}")
                return
            if current.status in (AgentStatus.FAILED, AgentStatus.CRASHED):
                console.print(f"[red]✗[/red] Agent failed: {current.error}")
                sys.exit(1)
        console.print("[yellow]⚠[/yellow] Timed out waiting for agent to start")


# ===========================================================================
# scale
# ===========================================================================


@main.command()
@click.argument("agent_id")
@click.argument("direction", type=click.Choice(["up", "down"]))
@click.option("--count", "-c", default=1, help="Number of replicas to add or remove.")
@click.option("--reason", "-r", help="Optional reason for audit logging.")
@click.pass_context
def scale(
    ctx: click.Context, agent_id: str, direction: str, count: int, reason: str | None
) -> None:
    """Manually scale an agent up or down (e.g. `agntspark scale agt_x up -c 2`)."""
    client = _get_client(ctx)

    with console.status(f"[bold blue]Scaling {agent_id} {direction}…"):
        result = client.agents.scale(agent_id, direction, count=count, reason=reason)

    table = Table(title="Scale Result", box=box.ROUNDED)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Agent ID", result.agent_id)
    table.add_row("Direction", result.direction.value)
    table.add_row("Previous Replicas", str(result.previous_replicas))
    table.add_row("Current Replicas", str(result.current_replicas))
    table.add_row("Status", _status_colored(result.status))
    console.print(table)


# ===========================================================================
# delete
# ===========================================================================


@main.command()
@click.argument("agent_id")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
def delete(ctx: click.Context, agent_id: str, yes: bool) -> None:
    """Permanently delete an agent and all its resources."""
    if not yes and not click.confirm(f"Permanently delete agent {agent_id!r}?"):
        console.print("[dim]Aborted.[/dim]")
        return

    client = _get_client(ctx)
    with console.status(f"[bold blue]Deleting {agent_id}…"):
        client.agents.delete(agent_id)
    console.print(f"[green]✓[/green] Agent [cyan]{agent_id}[/cyan] deleted.")


# ===========================================================================
# list
# ===========================================================================


@main.command()
@click.option("--status", "-s", help="Filter by status (running, stopped, failed).")
@click.option("--tag", "-t", help="Filter by tag.")
@click.option("--page", default=1, help="Page number.")
@click.option("--page-size", default=20, help="Results per page.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def list(  # noqa: A001 — this is the `agntspark list` subcommand name, by design
    ctx: click.Context,
    status: str | None,
    tag: str | None,
    page: int,
    page_size: int,
    as_json: bool,
) -> None:
    """List all agents."""
    client = _get_client(ctx)
    result = client.agents.list(status=status, tag=tag, page=page, page_size=page_size)

    if as_json:
        console.print_json(json.dumps([a.model_dump(mode="json") for a in result.agents]))
        return

    table = Table(title=f"Agents ({result.total} total)", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Status")
    table.add_column("Replicas", justify="right")
    table.add_column("Model", style="dim")
    table.add_column("Updated", style="dim")

    for agent in result.agents:
        table.add_row(
            agent.id,
            agent.name,
            _status_colored(agent.status),
            str(agent.replicas),
            agent.model,
            agent.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)

    if result.has_next:
        total_pages = (result.total + result.page_size - 1) // result.page_size
        console.print(
            f"[dim]Page {result.page} of {total_pages}. "
            f"Use --page {result.page + 1} for more.[/dim]"
        )


# ===========================================================================
# logs
# ===========================================================================


@main.command()
@click.argument("agent_id")
@click.option("--level", "-l", help="Filter by log level (DEBUG, INFO, WARN, ERROR).")
@click.option("--follow", "-f", is_flag=True, help="Follow log stream (requires --async).")
@click.option("--tail", "-n", default=50, help="Number of historical log lines to fetch.")
@click.pass_context
def logs(ctx: click.Context, agent_id: str, level: str | None, follow: bool, tail: int) -> None:
    """Fetch or follow agent logs."""
    client = _get_client(ctx)

    # Fetch historical logs
    historical = client.agents.logs(agent_id, level=level, limit=tail)

    if historical:
        for log in historical:
            _print_log(log)

    if not follow:
        return

    # Follow live stream
    console.print("[dim]Following live log stream… (Ctrl+C to stop)[/dim]")
    import asyncio

    from .streaming import EventType

    async def _follow():
        async with client as c:
            async for event in c.agents.stream_logs(agent_id, level=level):
                if event.type == EventType.LOG:
                    data = event.data
                    ts = data.get("timestamp", "")
                    lvl = data.get("level", "INFO")
                    msg = data.get("message", "")
                    replica = data.get("replica_id", "?")
                    _print_log_line(ts, lvl, replica, msg)

    try:
        asyncio.run(_follow())
    except KeyboardInterrupt:
        console.print("\n[dim]Log stream stopped.[/dim]")


# ===========================================================================
# metrics
# ===========================================================================


@main.command()
@click.argument("agent_id")
@click.option("--window", "-w", default="1h", help="Time window (5m, 1h, 24h, 7d).")
@click.pass_context
def metrics(ctx: click.Context, agent_id: str, window: str) -> None:
    """Show agent metrics."""
    client = _get_client(ctx)
    m = client.agents.metrics(agent_id, window=window)

    table = Table(title=f"Metrics for {agent_id} (window: {window})", box=box.ROUNDED)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("CPU", f"{m.cpu_percent:.1f}%")
    table.add_row("Memory", f"{m.memory_mb} MB ({m.memory_percent:.1f}%)")
    table.add_row("GPU", f"{m.gpu_percent:.1f}% ({m.gpu_memory_mb} MB)")
    table.add_row("Requests", f"{m.request_count} ({m.request_rate:.1f}/s)")
    table.add_row("Errors", f"{m.error_count} ({m.error_rate:.2f}/s)")
    table.add_row("P50 Latency", f"{m.p50_latency_ms:.1f} ms")
    table.add_row("P95 Latency", f"{m.p95_latency_ms:.1f} ms")
    table.add_row("P99 Latency", f"{m.p99_latency_ms:.1f} ms")
    table.add_row("Replicas", str(m.replicas))

    console.print(table)


# ===========================================================================
# Helpers
# ===========================================================================

_STATUS_COLORS = {
    AgentStatus.RUNNING: "green",
    AgentStatus.STARTING: "yellow",
    AgentStatus.BUILDING: "yellow",
    AgentStatus.SCALING: "blue",
    AgentStatus.STOPPING: "dim",
    AgentStatus.STOPPED: "dim",
    AgentStatus.PENDING: "yellow",
    AgentStatus.FAILED: "red",
    AgentStatus.CRASHED: "red",
}


def _status_colored(status: AgentStatus) -> str:
    color = _STATUS_COLORS.get(status, "white")
    return f"[{color}]{status.value}[/{color}]"


def _print_log(log) -> None:
    """Print a single log line with color formatting."""
    _print_log_line(
        log.timestamp.strftime("%H:%M:%S"),
        log.level,
        log.replica_id,
        log.message,
    )


def _print_log_line(ts: str, level: str, replica: str, msg: str) -> None:
    level_colors = {"ERROR": "red", "WARN": "yellow", "INFO": "green", "DEBUG": "dim"}
    color = level_colors.get(level, "white")
    console.print(f"[dim]{ts}[/dim] [{color}]{level:5s}[/{color}] [cyan]{replica[:8]}[/cyan] {msg}")


if __name__ == "__main__":
    main()
