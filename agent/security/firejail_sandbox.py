"""Firejail sandbox — isolation des outils shell via Firejail.

R1: Utilise firejail (package FOSS Debian) — pas de from scratch.
R5: Hard crash si firejail indisponible et sandbox requis.
R9: Un fichier = une fonction métier (ici: sandboxing Firejail).

Firejail isole l'exécution des commandes shell:
- Filesystem en lecture seule sur /etc, /usr, /bin
- Pas d'accès réseau (sauf explicite)
- Pas d'accès aux clés API (.env)
- /tmp isolé
- Pas de droits root

Usage:
    from security.firejail_sandbox import run_sandboxed
    result = run_sandboxed("ls -la /tmp", timeout=30)
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Profil Firejail minimal pour Local_Agent
FIREJAIL_PROFILE = """
# Local_Agent — profil Firejail minimal
# Sandbox les outils shell de l'agent

# Filesystem lecture seule (systeme)
read-only /etc
read-only /usr
read-only /bin
read-only /sbin
read-only /lib
read-only /lib64

# Pas d'acces aux clés API / secrets
read-only /home/moussa/Applications/Local_Agent/.env
blacklist /home/moussa/Applications/Local_Agent/.env
blacklist /home/moussa/Applications/Local_Agent/agent/telegram_router/.env
blacklist /home/moussa/.ssh
blacklist /home/moussa/.gnupg
blacklist /home/moussa/.aws

# /tmp isole
private-tmp

# Pas de reseau par defaut (les outils shell n'ont pas besoin de reseau)
# Pour les outils qui ont besoin de reseau, utiliser allow_network=True
# net none

# Pas de droits root
noroot

# Pas d'acces au noyau
blacklist /sys/kernel
blacklist /proc/kcore
blacklist /proc/kallsyms
"""

PROFILE_PATH = Path("/tmp/la_firejail.profile")


def _ensure_profile() -> Path:
    """Écrit (toujours) le profil Firejail — idempotent, reflète les MAJ du profil."""
    PROFILE_PATH.write_text(FIREJAIL_PROFILE)
    return PROFILE_PATH


def is_available() -> bool:
    """Vérifie si firejail est installé sur le système."""
    return shutil.which("firejail") is not None


def run_sandboxed(
    command: str,
    timeout: int = 30,
    allow_network: bool = False,
    workdir: str | None = None,
) -> dict:
    """Exécute une commande dans un sandbox Firejail.

    Args:
        command: La commande à exécuter (ex: "ls -la /tmp")
        timeout: Timeout en secondes
        allow_network: Si True, autorise le réseau (pour http_get_request, download_file)
        workdir: Dossier de travail (défaut: dossier courant)

    Returns:
        {"status": "success", "stdout": str, "stderr": str, "retcode": int}
        ou
        {"status": "error", "message": str}
        ou
        {"status": "unavailable", "message": "firejail not installed"}
    """
    if not is_available():
        logger.warning("firejail non installé — exécution non sandboxée")
        return {
            "status": "unavailable",
            "message": "firejail not installed — run: sudo apt install firejail",
        }

    profile = _ensure_profile()

    # Construire la commande firejail
    firejail_cmd = [
        "firejail",
        f"--profile={profile}",
        "--quiet",
    ]

    if not allow_network:
        firejail_cmd.append("--net=none")

    if workdir:
        firejail_cmd.append(f"--private-cwd={workdir}")

    # La commande à exécuter dans le sandbox
    # On utilise -- au lieu de shell pour éviter l'injection
    firejail_cmd.append("--")
    firejail_cmd.extend(shlex.split(command))

    try:
        result = subprocess.run(
            firejail_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "retcode": result.returncode,
            }
        else:
            return {
                "status": "error",
                "message": f"exit code {result.returncode}",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "retcode": result.returncode,
            }

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"timeout after {timeout}s"}
    except (FileNotFoundError, PermissionError, OSError) as e:
        return {"status": "error", "message": f"{type(e).__name__}: {e}"}


def validate_sandbox() -> dict:
    """Valide que le sandbox Firejail fonctionne correctement.

    Returns:
        {"available": bool, "version": str, "test_passed": bool, "details": str}
    """
    if not is_available():
        return {
            "available": False,
            "version": "",
            "test_passed": False,
            "details": "firejail not installed",
        }

    # Version
    try:
        version_result = subprocess.run(
            ["firejail", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = version_result.stdout.split("\n")[0] if version_result.stdout else ""
    except (subprocess.TimeoutExpired, OSError):
        version = "unknown"

    # Test: exécuter "echo test" dans le sandbox
    test_result = run_sandboxed("echo test_sandbox", timeout=10, allow_network=False)

    test_passed = (
        test_result["status"] == "success"
        and "test_sandbox" in test_result.get("stdout", "")
    )

    return {
        "available": True,
        "version": version,
        "test_passed": test_passed,
        "details": test_result.get("stdout", test_result.get("message", "")),
    }