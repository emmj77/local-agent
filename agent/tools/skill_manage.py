"""skill_manage — outil unifié de gestion des skills (list/view/add).

v21: Fusion de skill_manager_base + skill_view + skill_add.
Un seul fichier, un seul point d'entrée.

Actions:
  list  → {"action": "list"} — liste tous les skills
  view  → {"action": "view", "skill_id": "mon_skill"} — affiche un SKILL.md
  add   → {"action": "add", "skill_id": "...", "title": "...", "description": "...", ...}

R6: Interface d'outil (def run(json_args: str)).
R14: Template Jinja2 pour add.
"""
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from filelock import FileLock
from pydantic import BaseModel, Field

try:
    import yaml
except ImportError:
    yaml = None

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
_SKILLS_DIR = _AGENT_DIR.parent / "03_SKILLS"
_TEMPLATES_DIR = _AGENT_DIR / "core" / "templates"


class SkillManageArgs(BaseModel):
    action: str = Field("list", description="Action: list, view, add")
    skill_id: str = Field("", description="Identifiant (nom de dossier) du skill")
    title: str = Field("", description="Titre (action=add)")
    description: str = Field("", description="Description courte (action=add)")
    tags: list[str] = Field(default_factory=list, description="Tags (action=add)")
    steps: list[str] = Field(default_factory=list, description="Étapes (action=add)")
    prerequisites: str = Field("", description="Prérequis (action=add)")
    notes: str = Field("", description="Notes (action=add)")
    status: str = Field("draft", description="Statut: draft/active/archived (action=add)")


def _parse_frontmatter(content: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    if yaml:
        try:
            data = yaml.safe_load(block)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    fm = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def _action_list(args: dict) -> dict:
    """Liste tous les skills 03_SKILLS/<skill_id>/SKILL.md."""
    if not _SKILLS_DIR.exists():
        return {"status": "success", "count": 0, "skills": [], "message": "03_SKILLS/ vide"}
    skills = []
    for smd in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        sid = smd.parent.name
        fm = _parse_frontmatter(smd.read_text(encoding="utf-8", errors="replace"))
        skills.append({
            "skill_id": sid,
            "file": f"{sid}/SKILL.md",
            "name": fm.get("name", sid),
            "description": fm.get("description", ""),
            "status": fm.get("status", ""),
            "version": str(fm.get("version", "")),
            "tags": fm.get("tags", []),
        })
    return {"status": "success", "count": len(skills), "skills": skills}


def _action_view(args: dict) -> dict:
    """Retourne le contenu d'un SKILL.md."""
    skill_id = args.get("skill_id", "")
    if not skill_id:
        return {"status": "error", "message": "skill_id requis pour action=view"}
    if not _SKILLS_DIR.exists():
        return {"status": "error", "message": "03_SKILLS/ introuvable"}
    sid = skill_id.strip().replace("/SKILL.md", "").strip("/")
    smd = _SKILLS_DIR / sid / "SKILL.md"
    if not smd.exists():
        cands = [p for p in sorted(_SKILLS_DIR.glob("*/SKILL.md"))
                 if sid.lower() in p.parent.name.lower()]
        if not cands:
            return {"status": "error", "message": f"Skill '{sid}' introuvable"}
        smd = cands[0]
        sid = smd.parent.name
    content = smd.read_text(encoding="utf-8", errors="replace")
    return {"status": "success", "skill_id": sid, "file": f"{sid}/SKILL.md", "content": content}


def _action_add(args: dict) -> dict:
    """Crée un nouveau skill .md via template Jinja2."""
    skill_id = args.get("skill_id", "")
    if not skill_id:
        return {"status": "error", "message": "skill_id requis pour action=add"}
    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = _SKILLS_DIR / f"SKILL_{skill_id}.md"
    try:
        from jinja2.sandbox import SandboxedEnvironment
        from jinja2 import FileSystemLoader
        env = SandboxedEnvironment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
        template = env.get_template("skill_template.md.j2")
        content = template.render(
            skill_id=skill_id,
            title=args.get("title", ""),
            description=args.get("description", ""),
            tags=args.get("tags", []),
            date=datetime.now().isoformat(),
            status=args.get("status", "draft"),
            steps=args.get("steps", []),
            prerequisites=args.get("prerequisites", ""),
            notes=args.get("notes", ""),
        )
    except Exception as e:
        return {"status": "error", "message": f"Template error: {e}"}
    lock = FileLock(str(file_path) + ".lock")
    with lock:
        file_path.write_text(content, encoding="utf-8")
    return {"status": "success", "message": f"Skill créé: {file_path}"}


_ACTIONS = {
    "list": _action_list,
    "view": _action_view,
    "add": _action_add,
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