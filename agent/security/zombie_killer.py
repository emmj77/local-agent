"""Hooks de sécurité — zombie killer + crash backup + cache cleanup + garde-fou.

R5: Hard crash si tout échoue. Pas de masquage.
R9: Un fichier = une fonction métier (ici: hooks de cycle de vie).

Hooks implémentés (selon _IMPLEMENTER/hook_zombie_killer.md):
1. Zombie killer (atexit + signaux) — nettoie les subprocess enfants
2. Crash backup (sys.excepthook) — dump l'état avant le crash
3. Cache cleanup (atexit) — vide les fichiers temporaires de streaming
4. Garde-fou bash — valide les commandes avant exécution (couche supplémentaire)

Usage dans agent_server.py:
    from security.zombie_killer import install_hooks
    install_hooks(config)
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import sys
from pathlib import Path

import logging

logger = logging.getLogger(__name__)

# --- État global des processus enfants ---
_child_processes: list = []


def register_child(proc) -> None:
    """Enregistre un subprocess.Popen pour le nettoyage au shutdown.

    À appeler après chaque subprocess.Popen dans le code agent.
    """
    _child_processes.append(proc)


def unregister_child(proc) -> None:
    """Retire un processus de la liste (si terminé normalement)."""
    try:
        _child_processes.remove(proc)
    except ValueError:
        pass


# --- Hook 1: Zombie killer ---

def _hook_exterminateur() -> None:
    """Tue tous les processus enfants enregistrés.

    S'exécute via atexit — QUOI qu'il arrive à la fermeture.
    """
    for p in _child_processes:
        try:
            if p.poll() is None:  # Encore vivant
                p.kill()
                p.wait(timeout=3)
                logger.info("zombie_killed: pid=%s", p.pid)
        except (OSError, ValueError, TimeoutError):
            # Déjà mort ou injoignable
            pass


def _handle_signal(signum, frame) -> None:
    """SIGINT/SIGTERM — cleanup puis mort IMMÉDIATE et inconditionnelle.

    os._exit(0) au lieu de sys.exit(0) : sys.exit lève SystemExit qui ne perce
    pas un appel bloquant (LLM réseau) ni un event loop asyncio -> processus
    zombie qui ignore SIGTERM. os._exit tue le process net, sans attendre le
    déblocage. On lance quand même le cleanup des enfants avant.
    """
    logger.info("signal_received: %s — arrêt immédiat", signum)
    try:
        _hook_exterminateur()   # tue les subprocess enfants enregistrés
        _hook_nettoyage_cache()
    except Exception:
        pass
    os._exit(0)


# --- Hook 2: Crash backup ---

def _hook_exceptions_fatales(exc_type, value, traceback) -> None:
    """Intercepte les crashs non gérés et dump l'état avant de mourir.

    Évite la corruption de session en sauvegardant l'erreur.
    """
    logger.error("crash_intercepted: %s (%s)", str(value), exc_type.__name__)

    # Dump minimal dans le dossier sessions
    try:
        crash_file = Path("/tmp/la_crash_backup.json")
        crash_data = {
            "status": "crashed",
            "error": str(value),
            "type": exc_type.__name__,
        }
        crash_file.write_text(json.dumps(crash_data, indent=2))
    except OSError:
        pass  # Non bloquant — on crash de toute façon

    # Laisse le crash se produire normalement
    sys.__excepthook__(exc_type, value, traceback)


# --- Hook 3: Cache cleanup ---

_CACHE_DIRS = [
    "/tmp/la_stream_cache",
    "/tmp/la_agent_cache",
]


def _hook_nettoyage_cache() -> None:
    """Vide les dossiers de cache temporaires à la fermeture."""
    for cache_dir in _CACHE_DIRS:
        p = Path(cache_dir)
        if p.exists() and p.is_dir():
            for f in p.iterdir():
                try:
                    if f.is_file():
                        f.unlink()
                except OSError:
                    pass
            logger.info("cache_cleaned: %s", cache_dir)


# --- Hook 4: Garde-fou bash (validation pré-exécution) ---

_BLOCKED_PATTERNS = [
    r"rm\s+-(rf|fr|r)\s+/",
    r"chmod\s+777\s+/",
    r"mkfs",
    r"dd\s+if=/dev/zero",
    r":\(\)\{\s*:\|:\s*&\s*\};:",  # fork bomb
    r"shutdown",
    r"reboot",
]


def validate_command(command: str) -> str:
    """Valide une commande shell avant exécution.

    Couche supplémentaire au-dessus de command_blacklist.py.
    Lève PermissionError si la commande est dangereuse.

    Args:
        command: La commande brute à valider

    Returns:
        La commande si valide

    Raises:
        PermissionError: si un pattern interdit est détecté
    """
    import re

    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, command):
            raise PermissionError(
                f"Commande bloquée par le garde-fou: pattern '{pattern}' détecté"
            )

    return command


# --- Installation globale ---

def install_hooks(config=None) -> None:
    """Installe tous les hooks de sécurité.

    À appeler AU TOUT DÉBUT du script parent (agent_server.py, app.py, router_daemon.py).

    Args:
        config: InstanceConfig ou GlobalConfig (pour les chemins de cache/sessions)
    """
    # 1. Zombie killer via atexit
    atexit.register(_hook_exterminateur)

    # 2. Gestion des signaux (SIGINT + SIGTERM)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 3. Crash backup via sys.excepthook
    sys.excepthook = _hook_exceptions_fatales

    # 4. Cache cleanup via atexit
    atexit.register(_hook_nettoyage_cache)

    logger.info("hooks_installed: zombie_killer, signals, crash_backup, cache_cleanup")