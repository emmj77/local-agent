"""
command_blacklist.py — Liste statique des commandes destructrices.
Couche 1 de sécurité (ARCHITECTURE_UNIFIEE.txt lignes 506-508).

Bloque avant exécution toute commande contenant un pattern interdit.
Retourne la raison du blocage, ou None si la commande est autorisée.
"""
import re

# Patterns interdits (regex, case-insensitive)
BLACKLIST_PATTERNS = [
    # Suppression destructive — CATCH-ALL: tout rm récursif+forcé, quelle que soit la cible
    (r"\brm\s+(-\S*\s+)*-\S*r\S*f", "rm -rf (récursif+forcé) interdit"),
    (r"\brm\s+(-\S*\s+)*-\S*f\S*r", "rm -fr (forcé+récursif) interdit"),
    (r"\brm\b(?=.*\s-\S*r)(?=.*\s-\S*f)", "rm -r et -f (récursif+forcé) interdit"),
    (r"\brm\s+(-\S*\s+)*-\S*r\b", "rm récursif interdit (-r) — trop dangereux pour un agent"),
    (r"\brm\b.*--recursive", "rm --recursive interdit"),
    # Suppression destructive sur chemins sensibles (même non forcée)
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/", "rm -rf sur racine"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+\*", "rm -rf avec wildcard"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/home", "rm -rf sur /home"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/etc", "rm -rf sur /etc"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/usr", "rm -rf sur /usr"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/boot", "rm -rf sur /boot"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/var", "rm -rf sur /var"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/dev", "rm -rf sur /dev"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/sys", "rm -rf sur /sys"),
    (r"\brm\s+(-\w*\s+)*-r\w*f?\b.*\s+/proc", "rm -rf sur /proc"),
    # Formatage de disque
    (r"\bmkfs\b", "mkfs — formatage de disque"),
    # Écriture brute sur device
    (r"\bdd\b.*\bif=/dev/zero\b", "dd if=/dev/zero — écrasement"),
    (r"\bdd\b.*\bif=/dev/null\b", "dd if=/dev/null — écrasement"),
    (r"\bdd\b.*\bof=/dev/sd", "dd sur device bloc"),
    (r"\bdd\b.*\bof=/dev/nvm", "dd sur device NVMe"),
    # Effacement / vidage destructif (compléments denylist)
    (r"\bshred\b", "shred — effacement destructif irréversible"),
    (r"\bwipefs\b", "wipefs — efface les signatures de système de fichiers"),
    (r"\btruncate\b.*-s\s*0", "truncate -s0 — vidage de fichier"),
    (r"\bfind\b.*-delete\b", "find -delete — suppression massive"),
    (r"\bmkfs\b", "mkfs — formatage (doublon de sûreté)"),
    # Fork bomb
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}", "fork bomb"),
    (r"\bfork\s+bomb\b", "fork bomb explicite"),
    # Arrêt système
    (r"\bshutdown\b", "shutdown"),
    (r"\breboot\b", "reboot"),
    (r"\bhalt\b", "halt"),
    (r"\bpoweroff\b", "poweroff"),
    (r"\binit\s+0\b", "init 0"),
    # Permissions dangereuses
    (r"\bchmod\b.*\b777\b.*\s+/", "chmod 777 sur racine"),
    (r"\bchown\b.*\s+/\s*$", "chown sur racine"),
    # Écriture sur device bloc
    (r">\s*/dev/sd", "redirection vers device bloc"),
    # Élévation de privilèges suspecte
    (r"\bchmod\b.*\b[us]?\+?[sx]\b.*\s+/etc", "setuid/setgid sur /etc"),
    # Écrasement de fichiers système critiques
    (r">\s*/etc/(passwd|shadow|fstab|sudoers)", "écrasement fichier système"),
    (r">\s+/boot/", "écriture dans /boot"),
]


def is_blacklisted(command: str) -> str | None:
    """
    Vérifie si une commande contient un pattern interdit.

    Args:
        command: La commande shell à vérifier.

    Returns:
        La raison du blocage si la commande est interdite, None sinon.
    """
    cmd_lower = command.lower()
    for pattern, reason in BLACKLIST_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return reason
    return None


def check(command: str) -> dict:
    """
    Version outil: retourne un dict formaté.

    Returns:
        {"status": "ok"} ou {"status": "blocked", "reason": str}
    """
    reason = is_blacklisted(command)
    if reason:
        return {"status": "blocked", "reason": reason}
    return {"status": "ok"}