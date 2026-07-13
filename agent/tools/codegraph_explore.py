"""Outil codegraph_explore — interroge le graphe de code CodeGraph (lecture seule).

CodeGraph est un graphe de connaissance pré-indexé du code (symboles, appels,
dépendances). Cet outil est la MANIÈRE PRIVILÉGIÉE de lire/comprendre du code :
une seule requête renvoie le source verbatim pertinent + les call paths + le
blast radius, au lieu d'ouvrir les fichiers un par un.

Args JSON:
  {"query": "comment fonctionne l'agent_loop"}            -> explore (défaut)
  {"query": "agent_loop.py", "mode": "node"}              -> lit un fichier/symbole
                                                             (source + n° de ligne)
  {"query": "route_tool_call", "project": "supreme_pmc"}  -> autre projet indexé

mode: "explore" (défaut, question en langage naturel ou noms de symboles/fichiers)
      | "node" (source d'un symbole précis, ou lecture d'un fichier ligne à ligne).
project: "local_agent" (défaut) | "supreme_pmc" | chemin absolu vers un projet
         possédant un index .codegraph/.

R6 (run(json_args)), R9 (un fichier = une fonction métier). Lecture seule :
seules les sous-commandes codegraph explore/node sont appelées.
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field

# Projets indexés connus -> racine (là où vit le dossier .codegraph/).
_PROJECTS = {
    "local_agent": "/home/moussa/Applications/Local_Agent",
    "local": "/home/moussa/Applications/Local_Agent",
    "supreme_pmc": "/home/moussa/Documents/SUPREME_PMC",
    "supreme": "/home/moussa/Documents/SUPREME_PMC",
    "pmc": "/home/moussa/Documents/SUPREME_PMC",
}
_DEFAULT_PROJECT = "/home/moussa/Applications/Local_Agent"


class CodegraphArgs(BaseModel):
    query: str = Field(..., description="Question langage naturel, ou nom de symbole/fichier")
    mode: str = Field("explore", description="explore (défaut) | node")
    project: str = Field("local_agent", description="local_agent | supreme_pmc | chemin absolu")


def _resolve_project(p: str) -> Path:
    key = p.strip().lower()
    if key in _PROJECTS:
        root = Path(_PROJECTS[key])
    else:
        root = Path(p).expanduser().resolve()
    return root


def run(json_args: str):
    try:
        args = CodegraphArgs.model_validate_json(json_args)
    except (ValueError, TypeError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    if args.mode not in ("explore", "node"):
        print(json.dumps({"status": "error",
                          "message": f"mode invalide: {args.mode}. Attendu: explore | node."}))
        return

    cg = shutil.which("codegraph") or "/home/moussa/.local/bin/codegraph"
    if not Path(cg).exists():
        print(json.dumps({"status": "error",
                          "message": "codegraph introuvable (npm i -g @colbymchenry/codegraph)."}))
        return

    root = _resolve_project(args.project)
    if not (root / ".codegraph").is_dir():
        print(json.dumps({"status": "error",
                          "message": f"Pas d'index .codegraph/ dans {root}. "
                                     f"Projets connus: {sorted(set(_PROJECTS))}."}))
        return

    try:
        proc = subprocess.run(
            [cg, args.mode, args.query],
            cwd=str(root), capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "message": "codegraph timeout (120s)."}))
        return
    except OSError as e:
        print(json.dumps({"status": "error", "message": f"Exec codegraph: {e}"}))
        return

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        print(json.dumps({"status": "error", "message": err or f"codegraph exit {proc.returncode}"},
                         ensure_ascii=False))
        return

    print(json.dumps({"status": "success", "mode": args.mode,
                      "project": str(root), "result": out},
                     ensure_ascii=False))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
