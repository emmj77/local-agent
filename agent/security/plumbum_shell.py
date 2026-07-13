"""
plumbum_shell.py — Exécution shell blindée.

Deux régimes, dans cet ordre, TOUJOURS après les gardes (blacklist + garde-fou):
  - Commande SIMPLE (un seul binaire, pas d'opérateur) → plumbum : aucun shell
    n'est invoqué, injection bash impossible (défense maximale).
  - Commande COMPOSÉE (cd/&&/||/;/pipe/redirection/&) → bash -c, car plumbum ne
    sait exécuter qu'un binaire unique (`cd` est un builtin, `&&` non géré).
    Les gardes s'appliquent à la commande COMPLÈTE (opérateurs inclus), donc
    `cd /x && rm -rf /` reste bloqué par la blacklist avant d'atteindre bash.

Couche 2 de sécurité (ARCHITECTURE_UNIFIEE.txt lignes 510-513).
"""
import re
import shlex
import subprocess
from plumbum import local
from plumbum.commands import ProcessExecutionError
from plumbum.commands.processes import CommandNotFound

from .command_blacklist import is_blacklisted
from .zombie_killer import validate_command


class ShellError(Exception):
    """Erreur d'exécution shell (non-zero exit code)."""


# Opérateurs shell qui exigent un vrai shell : enchaînement (&&, ||, ;),
# pipe (|), arrière-plan (&), redirections (<, >), backtick et $(...).
_SHELL_OPS = re.compile(r'&&|\|\||[;|&<>`]|\$\(')


def run(command: str, timeout: int = 120) -> dict:
    """
    Exécute une commande shell de manière sécurisée.

    Returns:
        {"status": "success", "stdout": str, "stderr": str, "retcode": int}
        {"status": "blocked", "reason": str}    si blacklistée/garde-fou
        {"status": "error", "message": str}     si échec d'exécution

    Note: pour lancer un serveur en arrière-plan (`... &`), passer par les outils
    gpu_control (server_up) — un `&` capturé ici bloque jusqu'au timeout car le
    process détaché conserve le pipe stdout.
    """
    # Couche 1 : blacklist (commande complète, opérateurs inclus)
    blocked = is_blacklisted(command)
    if blocked:
        return {"status": "blocked", "reason": blocked}

    # Couche supplémentaire : garde-fou bash (regex patterns)
    try:
        validate_command(command)
    except PermissionError as e:
        return {"status": "blocked", "reason": str(e)}

    # Commande composée → bash -c (derrière les gardes)
    if _SHELL_OPS.search(command):
        return _run_bash(command, timeout)

    # Commande simple → plumbum (aucun shell)
    parts = shlex.split(command)
    if not parts:
        return {"status": "error", "message": "Commande vide"}
    try:
        cmd = local[parts[0]]
        # retcode=None : ne pas lever sur exit≠0, on gère le statut nous-mêmes.
        retcode, stdout, stderr = cmd.run(parts[1:], timeout=timeout, retcode=None)
        return {
            "status": "success" if retcode == 0 else "error",
            "stdout": stdout,
            "stderr": stderr,
            "retcode": retcode,
        }
    except (ProcessExecutionError, CommandNotFound, FileNotFoundError, PermissionError, TimeoutError, OSError) as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}


def _run_bash(command: str, timeout: int) -> dict:
    """Exécute une commande composée via `bash -c` (gardes déjà passées)."""
    try:
        p = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "status": "success" if p.returncode == 0 else "error",
            "stdout": p.stdout,
            "stderr": p.stderr,
            "retcode": p.returncode,
        }
    except subprocess.TimeoutExpired as e:
        return {"status": "error", "message": f"TimeoutExpired: {e}"}
    except OSError as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}
