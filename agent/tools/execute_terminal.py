"""
execute_terminal.py — Outil atomique: commande shell blindée.
Interface: def run(json_args: str): print(json.dumps(result))

Sécurité en couches (ARCHITECTURE_UNIFIEE.txt lignes 502-521):
  1. command_blacklist — bloque rm -rf, mkfs, dd, fork bomb, etc.
  2. plumbum_shell — exécute sans shell=True, anti-injection bash
  3. psutil_guard — vérifie CPU/RAM avant tâche lourde
  4. Validation Pydantic des arguments
  5. sandbox (OPT-IN) — si sandbox=true, exécution isolée Firejail
     (private-tmp, noroot, /etc,/usr read-only, secrets .env/.ssh/.gnupg/.aws
     bloqués, réseau autorisé). Défaut = comportement normal.

Marque distinctive: agent_id dans le JSON de retour.
"""
import sys
import os
import json
from pathlib import Path
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/ (exécution standalone)
from security.plumbum_shell import run as shell_run
from security.command_blacklist import check as blacklist_check
from security.psutil_guard import check_resources

# Ne PAS coder en dur l'agent_id (R8 agent unique paramétré) : le router/AgentLoop
# peut l'exposer via LA_AGENT_ID ; sinon vide (l'attribution réelle passe par log_local).
AGENT_ID = os.environ.get("LA_AGENT_ID", "")


class TerminalArgs(BaseModel):
    command: str
    sandbox: bool = Field(False, description="true = exécuter dans un sandbox Firejail (isolation)")


def run(json_args: str):
    try:
        args = TerminalArgs.model_validate_json(json_args)

        # Couche 1: blacklist
        bl = blacklist_check(args.command)
        if bl["status"] == "blocked":
            print(json.dumps({"status": "blocked", "reason": bl["reason"], "agent_id": AGENT_ID}))
            return

        # Couche 3: psutil_guard (avant exécution)
        resources = check_resources()
        if resources["status"] == "blocked":
            print(json.dumps({
                "status": "blocked", "reason": resources["reason"],
                "cpu_percent": resources["cpu_percent"],
                "ram_available_gb": resources["ram_available_gb"],
                "agent_id": AGENT_ID,
            }))
            return

        # Couche 5: sandbox Firejail (OPT-IN)
        if args.sandbox:
            from security.firejail_sandbox import run_sandboxed, is_available
            if not is_available():
                print(json.dumps({
                    "status": "error",
                    "message": "sandbox=true demandé mais firejail non installé "
                               "(sudo apt install firejail) — relance sans sandbox si tu acceptes l'exécution normale.",
                    "agent_id": AGENT_ID,
                }))
                return
            result = run_sandboxed(args.command, allow_network=True)
            result["agent_id"] = AGENT_ID
            result["sandboxed"] = True
            print(json.dumps(result))
            return

        # Couche 2: plumbum_shell (exécution sécurisée normale)
        result = shell_run(args.command)
        result["agent_id"] = AGENT_ID
        print(json.dumps(result))

    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e), "agent_id": AGENT_ID}))


if __name__ == "__main__":
    run(sys.argv[1])
