"""cron_manage — outil unifié de gestion des crons (list/purge/tools/write/report).

v21: Fusion de cron_manager + cron_writer + cron_report.
Un seul fichier, un seul point d'entrée.

Actions:
  list    → {"action": "list"} — liste les crons actifs et traités
  purge   → {"action": "purge"} — purge les crons processed de plus de 48h
  tools   → {"action": "tools"} — liste les outils du système cron
  write   → {"action": "write", "agent": "agent1", "prompt": "...", ...} — crée un cron .md
  report  → {"action": "report", "cron_meta": {...}, "response": "...", "status": "success"} — écrit un rapport

R6: Interface d'outil (def run(json_args: str)).
R14: Templates Jinja2 pour write/report.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
_VAULT_ROOT = _AGENT_DIR.parent
_CRON_DIR = _VAULT_ROOT / "07_CRON"
_TEMPLATES_DIR = _AGENT_DIR / "core" / "templates"
_REPORTS_DIR = _CRON_DIR / "cron_reports"
PURGE_HOURS = 48


class CronManageArgs(BaseModel):
    action: str = Field("list", description="Action: list, purge, tools, write, report")
    # write
    agent: str = Field("agent1", description="Agent cible (action=write)")
    project: str = Field("", description="Nom du projet (action=write)")
    tags: list[str] = Field(default_factory=list, description="Tags (action=write)")
    prompt: str = Field("", description="Instruction pour l'agent (action=write)")
    activation: str = Field("", description="Timestamp ISO d'activation (action=write)")
    # report
    cron_meta: dict = Field(default_factory=dict, description="Métadonnées du cron source (action=report)")
    response: str = Field("", description="Texte de réponse de l'agent (action=report)")
    status: str = Field("success", description="success ou error (action=report)")


def _get_env():
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import FileSystemLoader, select_autoescape
    return SandboxedEnvironment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(default=False),
        keep_trailing_newline=True,
    )


def _parse_cron_file(filepath: Path) -> dict | None:
    try:
        content = filepath.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        import yaml
        meta = yaml.safe_load(parts[1]) or {}
        meta["_file"] = filepath.name
        meta["_body"] = parts[2].strip()
        return meta
    except (ValueError, KeyError, RuntimeError, OSError, json.JSONDecodeError):
        return None


# --- Actions ---

def _action_list(args: dict) -> dict:
    active = []
    processed = []
    for f in sorted(_CRON_DIR.glob("cron_*.md")):
        if f.name.startswith("cron_report_"):
            continue
        meta = _parse_cron_file(f)
        if not meta:
            continue
        entry = {
            "file": f.name,
            "agent": meta.get("agent", ""),
            "project": meta.get("project", ""),
            "tags": meta.get("tags", []),
            "timestamp": meta.get("timestamp", ""),
            "activation": meta.get("activation", ""),
            "status": meta.get("status", "pending"),
            "prompt_preview": meta.get("_body", "")[:100],
        }
        if meta.get("status") == "processed":
            processed.append(entry)
        else:
            active.append(entry)
    return {
        "success": True,
        "active": active,
        "active_count": len(active),
        "processed": processed,
        "processed_count": len(processed),
    }


def _action_purge(args: dict) -> dict:
    import subprocess
    purged = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=PURGE_HOURS)
    for f in sorted(_CRON_DIR.glob("cron_*.md")):
        if f.name.startswith("cron_report_"):
            continue
        meta = _parse_cron_file(f)
        if not meta or meta.get("status") != "processed":
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                subprocess.run(["gio", "trash", str(f)], check=True, capture_output=True)
                purged.append(f.name)
            except (ValueError, KeyError, RuntimeError, OSError):
                pass
    return {"success": True, "purged": purged, "count": len(purged), "cutoff_hours": PURGE_HOURS}


def _action_tools(args: dict) -> dict:
    tools = [
        {"name": "cron_manage", "description": "Liste/purge/write/report les crons"},
    ]
    return {"success": True, "tools": tools}


def _action_write(args: dict) -> dict:
    prompt = args.get("prompt", "")
    if not prompt:
        return {"success": False, "error": "prompt requis pour action=write"}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    activation = args.get("activation", "") or now
    env = _get_env()
    template = env.get_template("cron_template.md.j2")
    content = template.render(
        agent=args.get("agent", "agent1"),
        project=args.get("project", ""),
        tags=args.get("tags", []),
        timestamp=now,
        activation=activation,
        prompt=prompt,
    )
    ts_short = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"cron_{ts_short}.md"
    filepath = _CRON_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return {"success": True, "file": str(filepath), "filename": filename}


def _action_report(args: dict) -> dict:
    cron_meta = args.get("cron_meta", {})
    response = args.get("response", "")
    status = args.get("status", "success")
    if status not in ("success", "error"):
        return {"success": False, "error": "status doit être 'success' ou 'error'"}
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ts_short = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    env = _get_env()
    template = env.get_template("cron_report_template.md.j2")
    content = template.render(
        agent=cron_meta.get("agent", ""),
        project=cron_meta.get("project", ""),
        tags=cron_meta.get("tags", []),
        timestamp=cron_meta.get("timestamp", ""),
        activation=cron_meta.get("activation", ""),
        completed=now,
        status=status,
        response=response,
    )
    filename = f"cron_report_{ts_short}.md"
    filepath = _REPORTS_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return {"success": True, "file": str(filepath), "filename": filename, "status": status}


_ACTIONS = {
    "list": _action_list,
    "purge": _action_purge,
    "tools": _action_tools,
    "write": _action_write,
    "report": _action_report,
}


def run(json_args: str = "{}"):
    try:
        args = json.loads(json_args)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON invalide: {e}"}))
        return
    action = args.get("action", "list")
    if action not in _ACTIONS:
        print(json.dumps({"success": False, "error": f"Action inconnue: '{action}'. Actions: {list(_ACTIONS.keys())}"}))
        return
    try:
        result = _ACTIONS[action](args)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError, ImportError) as e:
        print(json.dumps({"success": False, "error": str(e)}))


# --- API Python directe pour watcher.py ---

def list_active() -> list[dict]:
    active = []
    for f in sorted(_CRON_DIR.glob("cron_*.md")):
        if f.name.startswith("cron_report_"):
            continue
        meta = _parse_cron_file(f)
        if meta and meta.get("status") != "processed":
            active.append(meta)
    return active


def purge_old() -> int:
    import subprocess
    purged = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=PURGE_HOURS)
    for f in sorted(_CRON_DIR.glob("cron_*.md")):
        if f.name.startswith("cron_report_"):
            continue
        meta = _parse_cron_file(f)
        if not meta or meta.get("status") != "processed":
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                subprocess.run(["gio", "trash", str(f)], check=True, capture_output=True)
                purged += 1
            except (ValueError, KeyError, RuntimeError, OSError):
                pass
    return purged


def write_report(cron_meta: dict, response: str, status: str = "success") -> Path:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ts_short = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    env = _get_env()
    template = env.get_template("cron_report_template.md.j2")
    content = template.render(
        agent=cron_meta.get("agent", ""),
        project=cron_meta.get("project", ""),
        tags=cron_meta.get("tags", []),
        timestamp=cron_meta.get("timestamp", ""),
        activation=cron_meta.get("activation", ""),
        completed=now,
        status=status,
        response=response,
    )
    filepath = _REPORTS_DIR / f"cron_report_{ts_short}.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")