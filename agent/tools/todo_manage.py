"""todo_manage — outil unifié de gestion du TODO.md (template/write/check/end/list).

v21: Fusion de todo_write + todo_check + todo_end + todo_template.
Un seul fichier, un seul point d'entrée. ACCESSIBLE au routeur.

Actions:
  template → {"action": "template"} — crée/restaure un TODO.md vierge (5 slots)
  write    → {"action": "write", "line": 1, "text": "ma tâche"} — remplit une ligne
  check    → {"action": "check", "line": 1} — coche [ ] -> [x]
  end      → {"action": "end"} — restaure le template vierge
  list     → {"action": "list"} — lit et retourne le contenu du TODO.md

R6: Interface d'outil (def run(json_args: str)).
filelock pour protection.
"""
import sys
import json
import re
from datetime import datetime
from pathlib import Path
from filelock import FileLock
from pydantic import BaseModel, Field, field_validator

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
_TEMPLATES_DIR = _AGENT_DIR / "core" / "templates"
_TODO_FILE = _AGENT_DIR / "TODO.md"
AGENT_ID = _AGENT_DIR.name

_DEFAULT_TEMPLATE = (
    '---\nagent_id: "{aid}"\ndate: "{date}"\n---\n\n'
    '# TODO — {aid}\n\n'
    '1. [ ] \n2. [ ] \n3. [ ] \n4. [ ] \n5. [ ] \n'
)


class TodoManageArgs(BaseModel):
    action: str = Field("list", description="Action: template, write, check, end, list")
    line: int = Field(0, description="Numéro de ligne (1-5) pour write/check")
    text: str = Field("", description="Texte de la tâche (action=write)")

    @field_validator("line")
    @classmethod
    def validate_line(cls, v: int) -> int:
        if v < 0 or v > 5:
            raise ValueError("line doit être entre 0 et 5 (0 = non utilisé)")
        return v


def _action_template(args: dict) -> dict:
    """Crée ou restaure un TODO.md vierge avec 5 slots."""
    try:
        from jinja2.sandbox import SandboxedEnvironment
        from jinja2 import FileSystemLoader
        env = SandboxedEnvironment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
        template = env.get_template("todo_template.md.j2")
        content = template.render(agent_id=AGENT_ID, date=datetime.now().isoformat())
    except Exception:
        content = _DEFAULT_TEMPLATE.format(aid=AGENT_ID, date=datetime.now().isoformat())

    lock = FileLock(str(_TODO_FILE) + ".lock")
    with lock:
        _TODO_FILE.write_text(content, encoding="utf-8")
    return {"status": "success", "message": f"TODO.md créé: {_TODO_FILE}"}


def _action_write(args: dict) -> dict:
    """Remplit une ligne (1-5) du TODO.md."""
    line = args.get("line", 0)
    text = args.get("text", "")
    if line < 1 or line > 5:
        return {"status": "error", "message": "line doit être entre 1 et 5"}
    if not text:
        return {"status": "error", "message": "text requis pour action=write"}
    if not _TODO_FILE.exists():
        return {"status": "error", "message": "TODO.md introuvable. Lance action=template d'abord."}

    lock = FileLock(str(_TODO_FILE) + ".lock")
    with lock:
        lines = _TODO_FILE.read_text(encoding="utf-8").splitlines()
        pattern = re.compile(r"^(\d+\.\s*\[)\s*(\])\s*")
        replaced = False
        for i, l in enumerate(lines):
            match = pattern.match(l)
            if match and int(match.group(0).split(".")[0]) == line:
                lines[i] = f"{line}. [ ] {text}"
                replaced = True
                break
        if not replaced:
            return {"status": "error", "message": f"Ligne {line} non trouvée dans TODO.md"}
        _TODO_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "success", "message": f"Ligne {line} remplie: {text}"}


def _action_check(args: dict) -> dict:
    """Coche [ ] -> [x] sur une ligne du TODO.md."""
    line = args.get("line", 0)
    if line < 1 or line > 5:
        return {"status": "error", "message": "line doit être entre 1 et 5"}
    if not _TODO_FILE.exists():
        return {"status": "error", "message": "TODO.md introuvable."}

    lock = FileLock(str(_TODO_FILE) + ".lock")
    with lock:
        lines = _TODO_FILE.read_text(encoding="utf-8").splitlines()
        pattern = re.compile(rf"^{line}\.\s*\[\s\]\s")
        replaced = False
        for i, l in enumerate(lines):
            if pattern.match(l):
                lines[i] = l.replace("[ ]", "[x]", 1)
                replaced = True
                break
        if not replaced:
            return {"status": "error", "message": f"Ligne {line} déjà cochée ou introuvable"}
        _TODO_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "success", "message": f"Ligne {line} cochée"}


def _action_end(args: dict) -> dict:
    """Restaure le TODO.md vierge (alias de template)."""
    return _action_template(args)


def _action_list(args: dict) -> dict:
    """Lit et retourne le contenu du TODO.md."""
    if not _TODO_FILE.exists():
        return {"status": "error", "message": "TODO.md introuvable. Lance action=template d'abord."}
    content = _TODO_FILE.read_text(encoding="utf-8")
    return {"status": "success", "file": str(_TODO_FILE), "content": content}


_ACTIONS = {
    "template": _action_template,
    "write": _action_write,
    "check": _action_check,
    "end": _action_end,
    "list": _action_list,
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