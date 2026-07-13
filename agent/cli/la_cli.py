"""la_cli.py — CLI click pour interagir avec les agents depuis le terminal.

Commandes:
    la_cli send --agent <id> "prompt"     Envoyer un prompt à un agent
    la_cli status                         Voir l'état des agents
    la_cli logs --agent <id>              Derniers logs d'un agent
    la_cli tail --agent <id>              Suivre les events Pub/Sub en temps réel
    la_cli agents                         Lister les configs disponibles
    la_cli interactive [--agent <id>]     Mode REPL interactif

Usage:
    python -m agent.cli.la_cli send --agent agent1 "bonjour"
    python -m agent.cli.la_cli status
    python -m agent.cli.la_cli tail --agent agent1
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text

# Import path agent/
_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

from cli.redis_client import (
    send_to_agent,
    subscribe_events,
    get_agent_status,
    list_available_agents,
    get_agent_logs,
)

console = Console()


@click.group()
def cli():
    """Local_Agent CLI — interagir avec les agents depuis le terminal."""
    pass


@cli.command()
@click.option("--agent", "-a", required=True, help="ID de l'agent cible (ex: agent1)")
@click.option("--timeout", "-t", default=120000, help="Timeout en ms (défaut: 120000)")
@click.argument("prompt")
def send(agent: str, timeout: int, prompt: str):
    """Envoyer un prompt à un agent et afficher la réponse."""
    console.print(f"[dim]→ Envoi à {agent}...[/dim]")
    try:
        result = send_to_agent(agent, prompt, timeout_ms=timeout)
    except Exception as e:
        console.print(f"[red]ERREUR: {e}[/red]")
        sys.exit(1)

    if result.get("error"):
        console.print(f"[red]Agent {agent} a retourné une erreur:[/red]")
        console.print(result.get("text", ""))
        sys.exit(1)

    # Afficher la réponse
    panel = Panel(
        Markdown(result.get("text", "")),
        title=f"_agent: {result.get('agent_id', agent)}",
        border_style="cyan",
    )
    console.print(panel)

    meta = Table(show_header=False, box=None, padding=(0, 1))
    meta.add_row("outil:", str(result.get("tool_executed", "-")))
    meta.add_row("itérations:", str(result.get("iterations", 0)))
    meta.add_row("correlation_id:", str(result.get("correlation_id", "-")))
    console.print(meta)


@cli.command(name="status")
def status():
    """Vérifier l'état des agents (Redis accessible + configs)."""
    agents = list_available_agents()

    if not agents:
        console.print("[yellow]Aucune config trouvée dans agent/configs/[/yellow]")
        return

    table = Table(title="État des agents", border_style="cyan")
    table.add_column("Agent", style="bold")
    table.add_column("Modèle")
    table.add_column("Endpoint")
    table.add_column("Type")
    table.add_column("Redis", justify="center")

    for a in agents:
        alive = get_agent_status(a["agent_id"])
        redis_status = "[green]OK[/green]" if alive else "[red]OFF[/red]"
        table.add_row(
            a["agent_id"],
            a["model"],
            a["endpoint"],
            a["agent_type"],
            redis_status,
        )

    console.print(table)


@cli.command()
@click.option("--agent", "-a", required=True, help="ID de l'agent")
@click.option("--limit", "-n", default=20, help="Nombre de logs (défaut: 20)")
def logs(agent: str, limit: int):
    """Afficher les derniers logs d'un agent."""
    entries = get_agent_logs(agent, limit=limit)

    if not entries:
        console.print(f"[yellow]Aucun log pour {agent}[/yellow]")
        return

    for entry in reversed(entries):  # Plus récent en dernier
        ts = entry["timestamp"]
        cmd = entry["command"]
        out = entry["output"]
        console.print(f"[dim]{ts}[/dim] [bold cyan]{cmd}[/bold cyan]")
        # Tronquer l'output si trop long
        if len(out) > 2000:
            console.print(out[:2000] + "\n[dim]... (tronqué)[/dim]")
        else:
            console.print(out)
        console.print()


@cli.command()
@click.option("--agent", "-a", required=True, help="ID de l'agent à surveiller")
def tail(agent: str):
    """Suivre les events Pub/Sub d'un agent en temps réel (Ctrl+C pour stopper)."""
    console.print(f"[dim]Suivi des events de {agent} (Ctrl+C pour stopper)...[/dim]")
    console.print()

    try:
        for event in subscribe_events(agent):
            etype = event.get("type", "unknown")

            if etype == "tool_start":
                tool = event.get("tool", "?")
                args = event.get("args", {})
                args_str = ", ".join(f"{k}={v}" for k, v in args.items())
                console.print(f"[bold yellow]🔧 {tool}[/bold yellow]({args_str})")

            elif etype == "tool_result":
                tool = event.get("tool", "?")
                output = event.get("output", "")
                success = event.get("success", True)
                truncated = event.get("truncated", False)
                color = "green" if success else "red"
                status = "✓" if success else "✗"
                display = output if not truncated else output + " [dim](tronqué)[/dim]"
                console.print(f"[{color}]{status} {tool}[/{color}] {display}")

            elif etype == "stream_chunk":
                chunk = event.get("text", "")
                console.print(chunk, end="", style="dim")

            elif etype == "final":
                text = event.get("text", "")
                iters = event.get("iterations", 0)
                console.print()
                console.print(Panel(
                    Markdown(text),
                    title=f"[{event.get('agent_id', agent)}] final ({iters} itérations)",
                    border_style="cyan",
                ))

            else:
                console.print(f"[dim]{event}[/dim]")

    except KeyboardInterrupt:
        console.print("\n[dim]Arrêt du suivi.[/dim]")
    except Exception as e:
        console.print(f"\n[red]ERREUR: {e}[/red]")
        sys.exit(1)


@cli.command()
def agents():
    """Lister les configs d'agents disponibles."""
    agent_list = list_available_agents()

    if not agent_list:
        console.print("[yellow]Aucune config trouvée[/yellow]")
        return

    table = Table(title="Agents disponibles", border_style="cyan")
    table.add_column("Agent", style="bold")
    table.add_column("Modèle")
    table.add_column("Endpoint")
    table.add_column("Type")
    table.add_column("Config", style="dim")

    for a in agent_list:
        table.add_row(
            a["agent_id"],
            a["model"],
            a["endpoint"],
            a["agent_type"],
            Path(a["config_file"]).name,
        )

    console.print(table)


@cli.command()
@click.option("--agent", "-a", default=None, help="Agent cible par défaut (ex: agent1)")
def interactive(agent):
    """Mode REPL interactif — taper les prompts directement."""
    from cli.repl import run_repl
    run_repl(default_agent=agent)


if __name__ == "__main__":
    cli()