"""
psutil_guard.py — Vérifie la charge système avant une tâche lourde.
Couche 3 de sécurité (ARCHITECTURE_UNIFIEE.txt lignes 515-517).

Si CPU > 90% ou RAM disponible < 2 Go -> bloque l'exécution.
"""
import psutil

# Seuils par défaut (configurables)
DEFAULT_CPU_THRESHOLD = 90.0  # % d'utilisation CPU
DEFAULT_RAM_MIN_GB = 2.0      # Go de RAM disponible minimum


class ResourceError(Exception):
    """Système surchargé — exécution bloquée."""


def get_system_load() -> dict:
    """
    Retourne l'état actuel du système.

    Returns:
        {"cpu_percent": float, "ram_available_gb": float, "ram_total_gb": float}
    """
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    return {
        "cpu_percent": round(cpu, 1),
        "ram_available_gb": round(mem.available / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
    }


def check_resources(
    cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
    ram_min_gb: float = DEFAULT_RAM_MIN_GB,
) -> dict:
    """
    Vérifie si le système peut accepter une tâche lourde.

    Args:
        cpu_threshold: Seuil CPU en % (défaut 90).
        ram_min_gb: RAM disponible minimum en Go (défaut 2).

    Returns:
        {"status": "ok", "cpu_percent": float, "ram_available_gb": float}
        ou
        {"status": "blocked", "reason": str, "cpu_percent": float, "ram_available_gb": float}
    """
    load = get_system_load()
    cpu = load["cpu_percent"]
    ram = load["ram_available_gb"]

    if cpu > cpu_threshold:
        return {
            "status": "blocked",
            "reason": f"CPU surchargé: {cpu}% > {cpu_threshold}%",
            "cpu_percent": cpu,
            "ram_available_gb": ram,
        }

    if ram < ram_min_gb:
        return {
            "status": "blocked",
            "reason": f"RAM insuffisante: {ram} Go < {ram_min_gb} Go",
            "cpu_percent": cpu,
            "ram_available_gb": ram,
        }

    return {
        "status": "ok",
        "cpu_percent": cpu,
        "ram_available_gb": ram,
    }


def guard(
    cpu_threshold: float = DEFAULT_CPU_THRESHOLD,
    ram_min_gb: float = DEFAULT_RAM_MIN_GB,
) -> None:
    """
    Décorateur/gardien: lève ResourceError si le système est surchargé.
    À appeler avant d'exécuter une tâche lourde.
    """
    result = check_resources(cpu_threshold, ram_min_gb)
    if result["status"] == "blocked":
        raise ResourceError(result["reason"])