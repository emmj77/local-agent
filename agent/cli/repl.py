"""REPL interactif pour la CLI — boucle readline avec historique.

Commandes internes:
    /agent <id>    Changer d'agent cible
    /status        Voir l'état des agents
    /agents        Lister les agents
    /exit          Quitter (ou Ctrl+D)
    /help          Aide

L'historique est sauvegardé dans /tmp/la_conversation.log (pour compatibilité
avec des outils externes comme tail -f).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text

from cli.redis_client import (
    send_to_agent,
    get_agent_status,
    list_available_agents,
)

CONVERSATION_LOG = "/tmp/la_conversation.log"

console = Console()


def _append_log(text: str) -> None:
    """Écrit dans le fichier de conversation (pour tail -f externe)."""
    try:
        with open(CONVERSATION_LOG, "a") as f:
            f.write(text + "\n")
    except OSError:
        pass  # Non bloquant


def _show_help() -> None:
    console.print(Panel(
        "[bold]/agent <id>[/bold]  Changer d'agent cible\n"
        "[bold]/status[/bold]      Voir l'état des agents\n"
        "[bold]/agents[/bold]      Lister les agents\n"
        "[bold]/exit[/bold]        Quitter (ou Ctrl+D)\n"
        "[bold]/help[/bold]        Cette aide",
        title="Commandes",
        border_style="cyan",
    ))


def run_repl(default_agent: str | None = None) -> None:
    """Lance la boucle REPL interactive."""
    current_agent = default_agent or "agent1"

    console.print(Panel(
        f"Mode interactif — agent: [bold cyan]{current_agent}[/bold cyan]\n"
        "Tape /help pour les commandes, /exit pour quitter.",
        title="Local_Agent CLI",
        border_style="blue",
    ))

    # Vérifier que l'agent est vivant
    if not get_agent_status(current_agent):
        console.print(f"[yellow]⚠ Redis ne répond pas pour {current_agent} — les envois échoueront.[/yellow]")

    while True:
        try:
            # Prompt avec l'agent courant
            prompt_text = f"[bold cyan]{current_agent}[/bold cyan]> "
            user_input = console.input(prompt_text)

        except EOFError:
            console.print("\n[dim]Au revoir.[/dim]")
            break
        except KeyboardInterrupt:
            console.print("\n[dim]Ctrl+C — tape /exit pour quitter.[/dim]")
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        # Commandes internes
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()

            if cmd == "/exit":
                console.print("[dim]Au revoir.[/dim]")
                break

            elif cmd == "/help":
                _show_help()

            elif cmd == "/agent":
                if len(parts) < 2:
                    console.print("[red]Usage: /agent <id>[/red]")
                    continue
                new_agent = parts[1].strip()
                current_agent = new_agent
                alive = get_agent_status(current_agent)
                status_str = "[green]OK[/green]" if alive else "[red]OFF[/red]"
                console.print(f"Agent cible: [bold cyan]{current_agent}[/bold cyan] Redis: {status_str}")

            elif cmd == "/status":
                agents_list = list_available_agents()
                from rich.table import Table
                table = Table(border_style="cyan")
                table.add_column("Agent", style="bold")
                table.add_column("Modèle")
                table.add_column("Redis", justify="center")
                for a in agents_list:
                    alive = get_agent_status(a["agent_id"])
                    table.add_row(
                        a["agent_id"],
                        a["model"],
                        "[green]OK[/green]" if alive else "[red]OFF[/red]",
                    )
                console.print(table)

            elif cmd == "/agents":
                agents_list = list_available_agents()
                from rich.table import Table
                table = Table(border_style="cyan")
                table.add_column("Agent", style="bold")
                table.add_column("Modèle")
                table.add_column("Endpoint")
                for a in agents_list:
                    table.add_row(a["agent_id"], a["model"], a["endpoint"])
                console.print(table)

            else:
                console.print(f"[red]Commande inconnue: {cmd} (tape /help)[/red]")

            continue

        # Envoi du prompt à l'agent
        ts = datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim]{ts} → {current_agent}...[/dim]")
        _append_log(f"[{ts}] USER → {current_agent}: {user_input}")

        try:
            result = send_to_agent(current_agent, user_input)
        except Exception as e:
            console.print(f"[red]ERREUR: {e}[/red]")
            _append_log(f"[{ts}] ERROR: {e}")
            continue

        if result.get("error"):
            console.print(f"[red]Agent {current_agent} erreur:[/red]")
            console.print(result.get("text", ""))
            _append_log(f"[{ts}] ERROR: {result.get('text', '')}")
            continue

        # Afficher la réponse
        text = result.get("text", "")
        tool = result.get("tool_executed", "-")
        iters = result.get("iterations", 0)

        panel = Panel(
            Markdown(text),
            title=f"{result.get('agent_id', current_agent)} | outil: {tool} | {iters} itérations",
            border_style="cyan",
        )
        console.print(panel)

        # Log
        _append_log(f"[{ts}] {current_agent}: {text}")
        _append_log("")