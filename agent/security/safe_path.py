"""safe_path — R16: sécurité des accès fichiers.

Tout chemin fichier lu ou écrit par un outil DOIT être résolu via safe_path().
Vérifie que le chemin est sous le vault racine (Local_Agent/).
Lève FileNotInVaultError si hors vault.

Usage:
    from security.safe_path import safe_path
    p = safe_path("/home/moussa/Applications/Local_Agent/05_MEMORY/test.md")
    # -> Path absolue résolue
    # -> FileNotInVaultError si hors vault
"""

from __future__ import annotations

from pathlib import Path


class FileNotInVaultError(Exception):
    """Le chemin est hors du vault Local_Agent/."""


# Vault racine — fallback depuis __file__ (R18: devrait venir de config)
_VAULT_ROOT = Path(__file__).resolve().parent.parent.parent

# R18: peut être surchargé par config.paths.vault_root
def set_vault_root(vault_root: str | Path) -> None:
    """Met à jour le vault root global (depuis config.paths.vault_root)."""
    global _VAULT_ROOT
    _VAULT_ROOT = Path(vault_root).resolve()


def safe_path(path_str: str, vault_root: str | Path | None = None) -> Path:
    """Résout un chemin et vérifie qu'il est sous le vault.

    Args:
        path_str: Chemin à valider (absolu ou relatif)
        vault_root: Racine du vault (si None, utilise Local_Agent/)

    Returns:
        Path absolu résolu

    Raises:
        FileNotInVaultError: si le chemin résolu est hors du vault
    """
    root = Path(vault_root).resolve() if vault_root else _VAULT_ROOT
    p = Path(path_str).resolve()

    # Vérifier que p est sous root
    try:
        p.relative_to(root)
    except ValueError:
        raise FileNotInVaultError(
            f"Chemin hors vault: {p} (vault={root})"
        )

    return p


class PathBlockedError(Exception):
    """Le chemin vise une zone protégée (OS système ou /mnt/archives)."""


# Politique deny-list (choix Moussa 2026-07-11): autoriser tout SAUF l'OS
# système, /mnt/archives, et les dossiers de secrets du home. Contrairement à
# safe_path (vault-only), guarded_path laisse les agents travailler sur tous
# les projets (/home/moussa/Documents, DB_CENTRALE, /tmp…) tout en bloquant
# la lecture/écriture des zones sensibles (anti path-traversal).
_HOME = Path.home()
_VAULT = Path("/home/moussa/Applications/Local_Agent")
_BLOCKED_ROOTS = [
    Path(p) for p in (
        "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32",
        "/boot", "/root", "/sys", "/proc", "/dev", "/run", "/srv",
        "/var", "/opt", "/mnt/archives",
    )
] + [
    # Dossiers de secrets
    _HOME / ".ssh", _HOME / ".gnupg", _HOME / ".aws",
    # Dotfiles d'exécution / persistance (un LLM en boucle ne doit pas pouvoir
    # écraser ton shell ni s'auto-démarrer)
    _HOME / ".bashrc", _HOME / ".bash_profile", _HOME / ".profile",
    _HOME / ".zshrc", _HOME / ".config/autostart", _HOME / ".config/systemd",
    # Secrets du projet
    _VAULT / ".env", _HOME / ".git-credentials",
]


def guarded_path(path_str: str) -> Path:
    """Résout un chemin et le refuse s'il vise une zone protégée.

    Autorise tout le reste (home, projets, /tmp). Lève PathBlockedError sinon.
    resolve() suit les symlinks → pas d'évasion via lien symbolique.
    """
    p = Path(path_str).resolve()
    for b in _BLOCKED_ROOTS:
        try:
            br = b.resolve()
        except OSError:
            br = b
        if p == br or br in p.parents:
            raise PathBlockedError(f"Accès refusé (zone protégée {br}): {p}")
    return p


def safe_join(*parts: str, vault_root: str | Path | None = None) -> Path:
    """Joint des parties de chemin et valide le résultat.

    Args:
        *parts: Parties du chemin (ex: "05_MEMORY", "test.md")
        vault_root: Racine du vault

    Returns:
        Path absolu résolu et validé

    Raises:
        FileNotInVaultError: si le résultat est hors vault
    """
    root = Path(vault_root).resolve() if vault_root else _VAULT_ROOT
    p = root.joinpath(*parts).resolve()
    return safe_path(str(p), vault_root=root)