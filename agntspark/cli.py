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
    agntspark invoke <agent> <message> # Call an agent (--key for private agents)
    agntspark access <agent-id> [mode] # Show/change public or private, --rpm
    agntspark keys create|list|revoke  # Manage a private agent's access keys
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
    default="https://agntapi.agntspark.com/v1",
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
            api_key=cfg_data.get("api_key"),
            access=cfg_data.get("access"),
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
    table.add_row("Access", deployed.access.value)
    console.print(table)
    if agent.access_key:
        console.print(
            f"[yellow]Access key (shown once):[/yellow] {agent.access_key}\n"
            "[dim]Send it as 'Authorization: Bearer <key>' when calling the agent.[/dim]"
        )

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
# invoke
# ===========================================================================


@main.command()
@click.argument("agent")
@click.argument("message")
@click.option(
    "--key",
    "-k",
    "access_key",
    envvar="AGNTSPARK_AGENT_KEY",
    help="The agent's access key (agk_…); needed for private agents.",
)
@click.option("--session", "-s", "session_id", help="Continue an earlier conversation.")
@click.pass_context
def invoke(
    ctx: click.Context, agent: str, message: str, access_key: str | None, session_id: str | None
) -> None:
    """Send MESSAGE to AGENT (an agent ID or URL) and print the reply."""
    client = _get_client(ctx)
    try:
        reply = client.agents.invoke(agent, message, session_id=session_id, access_key=access_key)
    except AgntSparkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    # The reply is model output: print it as-is, not as Rich markup.
    console.print(reply.output, markup=False, highlight=False)
    if reply.session_id:
        console.print(f"[dim]session: {reply.session_id}[/dim]")


# ===========================================================================
# access / keys
# ===========================================================================


@main.command()
@click.argument("agent_id")
@click.argument("mode", type=click.Choice(["public", "private"]), required=False)
@click.option(
    "--rpm",
    type=click.IntRange(1, 100_000),
    help="Requests per minute allowed from one caller IP.",
)
@click.option("--default-rpm", is_flag=True, help="Use the platform's default per-caller limit.")
@click.pass_context
def access(
    ctx: click.Context, agent_id: str, mode: str | None, rpm: int | None, default_rpm: bool
) -> None:
    """Show or change who may call an agent (public/private), and how fast."""
    if rpm is not None and default_rpm:
        raise click.UsageError("Use --rpm or --default-rpm, not both.")
    client = _get_client(ctx)
    try:
        if rpm is not None:
            agent = client.agents.update(agent_id, access=mode, rate_limit_rpm=rpm)
        elif default_rpm:
            agent = client.agents.update(agent_id, access=mode, rate_limit_rpm=None)
        elif mode is not None:
            agent = client.agents.update(agent_id, access=mode)
        else:
            agent = client.agents.get(agent_id)
    except AgntSparkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    limit = agent.rate_limit_rpm if agent.rate_limit_rpm is not None else "default"
    console.print(f"{agent.id}: [cyan]{agent.access.value}[/cyan], {limit} requests/min per caller")


@main.group()
def keys() -> None:
    """Manage a private agent's access keys."""


@keys.command("create")
@click.argument("agent_id")
@click.option("--label", "-l", default="default", show_default=True, help="What the key is for.")
@click.pass_context
def keys_create(ctx: click.Context, agent_id: str, label: str) -> None:
    """Create an access key. It's shown only once."""
    client = _get_client(ctx)
    try:
        created = client.agents.create_access_key(agent_id, label=label)
    except AgntSparkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    console.print(
        f"[green]✓[/green] Created access key [cyan]{created.label}[/cyan] ({created.id})"
    )
    console.print(created.key, markup=False, highlight=False)
    console.print("[yellow]Copy it now: it won't be shown again.[/yellow]")


@keys.command("list")
@click.argument("agent_id")
@click.pass_context
def keys_list(ctx: click.Context, agent_id: str) -> None:
    """List an agent's access keys."""
    client = _get_client(ctx)
    try:
        found = client.agents.list_access_keys(agent_id)
    except AgntSparkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    if not found:
        console.print("[dim]No access keys.[/dim]")
        return
    table = Table(title=f"Access keys for {agent_id}", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Label", style="cyan")
    table.add_column("Key")
    table.add_column("Created")
    for k in found:
        table.add_row(k.id, k.label, k.key_preview, k.created_at.strftime("%Y-%m-%d"))
    console.print(table)


@keys.command("revoke")
@click.argument("agent_id")
@click.argument("key_id")
@click.pass_context
def keys_revoke(ctx: click.Context, agent_id: str, key_id: str) -> None:
    """Revoke an access key; requests using it are refused from then on."""
    client = _get_client(ctx)
    try:
        client.agents.delete_access_key(agent_id, key_id)
    except AgntSparkError as exc:
        console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    console.print(f"[green]✓[/green] Revoked access key {key_id}")


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
