"""project_manage — outil unifié de gestion de projets (list/view/add/rapport).

v21: Fusion de project_manager_base + project_view + project_add + project_rapport.
Un seul fichier, un seul import, un seul point d'entrée.

Actions:
  list     → {"action": "list"}
  view     → {"action": "view", "project_id": "MODELE_RA"}
  add      → {"action": "add", "project_id": "mon_projet", "title": "...", ...}
  rapport  → {"action": "rapport", "project_id": "MODELE_RA", "status": "...", ...}

R6: Interface d'outil (def run(json_args: str)).
R14: Templates Jinja2 SandboxedEnvironment pour add/rapport.
"""
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from filelock import FileLock
from pydantic import BaseModel, Field

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
_PROJECTS_DIR = _AGENT_DIR.parent / "02_PROJETS"
_TEMPLATES_DIR = _AGENT_DIR / "core" / "templates"


# --- Modèle Pydantic ---

class ProjectManageArgs(BaseModel):
    action: str = Field("list", description="Action: list, view, add, rapport")
    project_id: str = Field("", description="ID du projet (action=view/add/rapport)")
    title: str = Field("", description="Titre (action=add)")
    summary: str = Field("", description="Résumé (action=add)")
    context: str = Field("", description="Contexte (action=add)")
    objectives: list[str] = Field(default_factory=list, description="Objectifs (action=add)")
    status: str = Field("active", description="Statut (action=add)")
    status_detail: str = Field("", description="Détail statut (action=add)")
    blockers: str = Field("", description="Blocages (action=add/rapport)")
    next_steps: list[str] = Field(default_factory=list, description="Prochaines étapes (action=add/rapport)")
    tags: list[str] = Field(default_factory=list, description="Tags (action=add)")
    project_title: str = Field("", description="Titre projet (action=rapport)")
    progress: str = Field("", description="Avancement (action=rapport)")


# --- Utils ---

def _parse_frontmatter(content: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


# --- Actions ---

def _action_list(args: dict) -> dict:
    """Liste tous les projets dans 02_PROJETS/."""
    if not _PROJECTS_DIR.exists():
        return {"status": "success", "projects": [], "message": "02_PROJETS/ vide"}
    projects = []
    for d in sorted(_PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        proj_file = d / "PROJET.md"
        if not proj_file.exists():
            continue
        content = proj_file.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        projects.append({
            "file": f"{d.name}/PROJET.md",
            "project_id": fm.get("project_id", d.name),
            "title": fm.get("title", ""),
            "status": fm.get("status", ""),
        })
    return {"status": "success", "count": len(projects), "projects": projects}


def _action_view(args: dict) -> dict:
    """Retourne le contenu d'un projet .md."""
    project_id = args.get("project_id", "")
    if not project_id:
        return {"status": "error", "message": "project_id requis pour action=view"}
    if not _PROJECTS_DIR.exists():
        return {"status": "error", "message": "02_PROJETS/ introuvable"}
    candidates = list(_PROJECTS_DIR.glob(f"*{project_id}*/PROJET.md"))
    if not candidates:
        return {"status": "error", "message": f"Projet '{project_id}' introuvable"}
    content = candidates[0].read_text(encoding="utf-8")
    return {"status": "success", "file": candidates[0].name, "content": content}


def _action_add(args: dict) -> dict:
    """Crée un nouveau projet .md via template Jinja2."""
    project_id = args.get("project_id", "")
    if not project_id:
        return {"status": "error", "message": "project_id requis pour action=add"}
    proj_dir = _PROJECTS_DIR / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    file_path = proj_dir / "PROJET.md"
    try:
        from jinja2 import FileSystemLoader
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
        template = env.get_template("project_template.md.j2")
        content = template.render(
            project_id=project_id,
            title=args.get("title", ""),
            status=args.get("status", "active"),
            date=datetime.now().isoformat(),
            tags=args.get("tags", []),
            summary=args.get("summary", ""),
            context=args.get("context", ""),
            objectives=args.get("objectives", []),
            status_detail=args.get("status_detail", ""),
            blockers=args.get("blockers", ""),
            next_steps=args.get("next_steps", []),
        )
    except Exception as e:
        return {"status": "error", "message": f"Template error: {e}"}
    lock = FileLock(str(file_path) + ".lock")
    with lock:
        file_path.write_text(content, encoding="utf-8")
    return {"status": "success", "message": f"Projet créé: {file_path}"}


def _action_rapport(args: dict) -> dict:
    """Génère un rapport formaté sur le projet actif."""
    project_id = args.get("project_id", "")
    if not project_id:
        return {"status": "error", "message": "project_id requis pour action=rapport"}
    project_title = args.get("project_title", "")
    if not project_title:
        candidates = list(_PROJECTS_DIR.glob(f"*{project_id}*/PROJET.md"))
        if candidates:
            content = candidates[0].read_text(encoding="utf-8")
            fm = _parse_frontmatter(content)
            project_title = fm.get("title", project_id)
    try:
        from jinja2 import FileSystemLoader
        from jinja2.sandbox import SandboxedEnvironment
        env = SandboxedEnvironment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
        template = env.get_template("project_rapport_template.md.j2")
        content = template.render(
            project_id=project_id,
            project_title=project_title or project_id,
            date=datetime.now().isoformat(),
            status=args.get("status", ""),
            progress=args.get("progress", ""),
            blockers=args.get("blockers", ""),
            next_steps=args.get("next_steps", []),
        )
    except Exception as e:
        return {"status": "error", "message": f"Template error: {e}"}
    rapports_dir = _PROJECTS_DIR / "RAPPORTS"
    rapports_dir.mkdir(parents=True, exist_ok=True)
    rapport_name = f"RAPPORT_{project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    file_path = rapports_dir / rapport_name
    lock = FileLock(str(file_path) + ".lock")
    with lock:
        file_path.write_text(content, encoding="utf-8")
    return {"status": "success", "message": f"Rapport généré: {file_path}"}


_ACTIONS = {
    "list": _action_list,
    "view": _action_view,
    "add": _action_add,
    "rapport": _action_rapport,
}


def run(json_args: str = "{}"):
    try:
        args = json.loads(json_args)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"JSON invalide: {e}"}))
        return
    action = args.get("action", "list")
    if action not in _ACTIONS:
        print(json.dumps({
            "status": "error",
            "message": f"Action inconnue: '{action}'. Actions: {list(_ACTIONS.keys())}",
        }))
        return
    try:
        result = _ACTIONS[action](args)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError, ImportError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")