"""Outil file_manage — fusion de 8 outils fichiers en un seul dispatch par action.

Actions: read, write, append, delete, metadata, list, search, grep.

SÉCURITÉ:
  - Tous les chemins passent par guarded_path (R16: refuse OS système, /mnt/archives,
    secrets du home, dotfiles d'exécution).
  - read: refuse les fichiers de code (redirige vers codegraph_explore).
  - write: sauvegarde horodatée avant écrasement (anti-perte).
  - delete: déplace vers la corbeille (gio trash), jamais de rm définitif.

Interface: run(json_args: str) → print(JSON).
"""

import sys
import json
import os
import re
import shutil
import subprocess
import datetime
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/
from security.safe_path import guarded_path, PathBlockedError

# ---------------------------------------------------------------------------
# Extensions de code indexées par CodeGraph → lecture directe INTERDITE
# ---------------------------------------------------------------------------
_CODE_EXT = {
    ".py", ".pyi", ".rs", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".go", ".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".hh", ".hxx",
    ".cs", ".java", ".kt", ".kts", ".scala", ".swift", ".m", ".mm",
    ".rb", ".php", ".lua", ".dart", ".vue", ".svelte", ".astro",
    ".sh", ".bash", ".zsh", ".r", ".sol", ".tf", ".ex", ".exs", ".erl",
    ".cu", ".cuh", ".metal", ".vb",
}

# Backup pour write
_BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "06_LOGS" / "write_backups"

# Plafond search
_MAX_RESULTS = 200


# ---------------------------------------------------------------------------
# Modèle Pydantic unifié
# ---------------------------------------------------------------------------
class FileManageArgs(BaseModel):
    action: Literal["read", "write", "append", "delete", "metadata", "list", "search", "grep"] = \
        Field(..., description="Action à exécuter")
    file_path: Optional[str] = Field(None, description="Chemin vers le fichier (read, write, append, delete, metadata, grep)")
    content: Optional[str] = Field(None, description="Contenu à écrire ou ajouter (write, append)")
    path: Optional[str] = Field(None, description="Répertoire à lister (list)")
    root_dir: Optional[str] = Field(None, description="Racine de la recherche (search)")
    pattern: Optional[str] = Field(None, description="Motif de recherche (search, grep)")


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------
def _backup_if_needed(path: Path) -> str | None:
    """Sauvegarde le contenu existant avant écrasement. Retourne le chemin du backup."""
    if not path.exists() or not path.is_file():
        return None
    old = path.read_bytes()
    if not old.strip():
        return None  # fichier vide : rien à protéger
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = _BACKUP_DIR / f"{path.name}.{ts}.bak"
    dest.write_bytes(old)
    return str(dest)


# ---------------------------------------------------------------------------
# Handlers par action
# ---------------------------------------------------------------------------
def _action_read(args: FileManageArgs):
    if not args.file_path:
        print(json.dumps({"status": "error", "message": "Champ 'file_path' requis pour action 'read'"}))
        return
    try:
        path = guarded_path(args.file_path)
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        return

    if path.suffix.lower() in _CODE_EXT:
        print(json.dumps({
            "status": "error",
            "message": (
                f"Lecture directe des fichiers de code désactivée ({path.suffix}). "
                f"Utilise l'outil codegraph_explore pour lire/comprendre le code. "
                f"Ex — lire ce fichier ligne à ligne: "
                f"codegraph_explore {{\"query\": \"{path.name}\", \"mode\": \"node\"}} ; "
                f"ou une question: codegraph_explore {{\"query\": \"...\", \"mode\": \"explore\"}}. "
                f"Précise \"project\": \"supreme_pmc\" si le fichier est hors de Local_Agent."
            ),
            "redirect_tool": "codegraph_explore",
        }, ensure_ascii=False))
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            print(json.dumps({"status": "success", "content": f.read()}))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_write(args: FileManageArgs):
    if not args.file_path or args.content is None:
        print(json.dumps({"status": "error", "message": "Champs 'file_path' et 'content' requis pour action 'write'"}))
        return
    try:
        path = guarded_path(args.file_path)
        old_size = path.stat().st_size if path.exists() and path.is_file() else 0
        backup = _backup_if_needed(path)
        content = args.content.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        new_size = len(content.encode("utf-8"))

        msg = {"status": "success", "message": "File written"}
        if backup:
            msg["backup"] = backup
        # Alerte si on réduit fortement un fichier existant (clobber probable)
        if old_size > 200 and new_size < old_size * 0.5:
            msg["warning"] = (
                f"⚠️ Fichier RÉDUIT de {old_size} à {new_size} octets "
                f"(-{100 - new_size * 100 // old_size}%). write ÉCRASE tout. "
                f"Si tu voulais AJOUTER du contenu, relis d'abord le fichier et "
                f"réécris-le ENTIER. Backup: {backup}"
            )
        print(json.dumps(msg, ensure_ascii=False))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_append(args: FileManageArgs):
    if not args.file_path or args.content is None:
        print(json.dumps({"status": "error", "message": "Champs 'file_path' et 'content' requis pour action 'append'"}))
        return
    try:
        path = guarded_path(args.file_path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(args.content)
        print(json.dumps({"status": "success"}))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_delete(args: FileManageArgs):
    if not args.file_path:
        print(json.dumps({"status": "error", "message": "Champ 'file_path' requis pour action 'delete'"}))
        return
    try:
        p = guarded_path(args.file_path)
        if not p.exists():
            print(json.dumps({"status": "error", "message": f"Introuvable: {p}"}))
            return
        try:
            r = subprocess.run(["gio", "trash", str(p)], capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            print(json.dumps({"status": "error",
                              "message": "gio introuvable (session graphique absente ?). "
                                         "Suppression annulée — aucun rm définitif effectué."}))
            return
        if r.returncode != 0:
            print(json.dumps({"status": "error",
                              "message": f"Échec corbeille: {r.stderr.strip() or 'gio trash'}"}))
            return
        print(json.dumps({"status": "success", "message": f"Déplacé vers la corbeille: {p}"}))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_metadata(args: FileManageArgs):
    if not args.file_path:
        print(json.dumps({"status": "error", "message": "Champ 'file_path' requis pour action 'metadata'"}))
        return
    try:
        p = guarded_path(args.file_path)
        if not p.exists():
            print(json.dumps({"status": "error", "message": f"Introuvable: {p}"}))
            return
        stat = p.stat()
        print(json.dumps({
            "status": "success",
            "path": str(p),
            "size": stat.st_size,
            "modified": str(datetime.datetime.fromtimestamp(stat.st_mtime)),
        }, ensure_ascii=False))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_list(args: FileManageArgs):
    dir_path = args.path or "."
    try:
        p = guarded_path(dir_path)
        if not p.is_dir():
            print(json.dumps({"status": "error", "message": f"Pas un répertoire: {p}"}))
            return
        files = sorted(f.name for f in p.iterdir())
        print(json.dumps({"status": "success", "path": str(p), "files": files}, ensure_ascii=False))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_search(args: FileManageArgs):
    if not args.root_dir or not args.pattern:
        print(json.dumps({"status": "error", "message": "Champs 'root_dir' et 'pattern' requis pour action 'search'"}))
        return
    try:
        root = guarded_path(args.root_dir)
        if not root.is_dir():
            print(json.dumps({"status": "error", "message": f"Pas un répertoire: {root}"}))
            return
        matches = []
        for r, _, files in os.walk(root):
            for f in files:
                if args.pattern in f:
                    matches.append(str(Path(r) / f))
                    if len(matches) >= _MAX_RESULTS:
                        matches.append(f"... (plafond {_MAX_RESULTS} atteint)")
                        break
            if len(matches) >= _MAX_RESULTS:
                break
        print(json.dumps({"status": "success", "count": len(matches), "matches": matches}, ensure_ascii=False))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, OSError, FileNotFoundError, PermissionError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


def _action_grep(args: FileManageArgs):
    if not args.file_path or not args.pattern:
        print(json.dumps({"status": "error", "message": "Champs 'file_path' et 'pattern' requis pour action 'grep'"}))
        return
    try:
        path = guarded_path(args.file_path)
        with open(path, "r", encoding="utf-8") as f:
            matches = [line.strip() for line in f if re.search(args.pattern, line)]
        print(json.dumps({"status": "success", "matches": matches}, ensure_ascii=False))
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, re.error, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_DISPATCH = {
    "read": _action_read,
    "write": _action_write,
    "append": _action_append,
    "delete": _action_delete,
    "metadata": _action_metadata,
    "list": _action_list,
    "search": _action_search,
    "grep": _action_grep,
}


def run(json_args: str):
    """Point d'entrée unique. Parse JSON, valide via FileManageArgs, dispatch."""
    try:
        args = FileManageArgs.model_validate_json(json_args)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    handler = _DISPATCH.get(args.action)
    if handler is None:
        print(json.dumps({"status": "error", "message": f"Action inconnue: {args.action}"}))
        return
    handler(args)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")