"""session_manage — outil unifié de gestion des sessions (search/summary).

v21: Fusion de session_search + session_summary_view.
Lit les fichiers plats 04_SESSIONS/sessions_index.json + .md.

Actions:
  search  → {"action": "search", "count": 10, "agent_id": "agent1", "query": "..."}
  summary → {"action": "summary", "session_id": "sess_..."}

R6: Interface d'outil (def run(json_args: str)).
R9: Un fichier = une fonction métier.
"""
import sys
import json
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

_VAULT_ROOT = Path(__file__).resolve().parents[2]
_SESSIONS_DIR = _VAULT_ROOT / "04_SESSIONS"
_INDEX_PATH = _SESSIONS_DIR / "sessions_index.json"


class SessionManageArgs(BaseModel):
    action: str = Field("search", description="Action: search, summary")
    # search
    count: int = Field(10, description="Nombre de sessions (action=search, max 30)")
    agent_id: str | None = Field(None, description="Filtrer par agent_id (action=search)")
    query: str | None = Field(None, description="Recherche textuelle (action=search)")
    # summary
    session_id: str = Field("", description="ID de session (action=summary)")

    @field_validator("count")
    @classmethod
    def validate_count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("count doit être >= 1")
        if v > 30:
            raise ValueError("count max 30")
        return v


def _load_index() -> list[dict]:
    if not _INDEX_PATH.exists():
        return []
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _action_search(args: dict) -> dict:
    count = args.get("count", 10)
    agent_id = args.get("agent_id")
    query = args.get("query")

    if not _INDEX_PATH.exists():
        return {"status": "error", "message": "sessions_index.json introuvable"}

    entries = _load_index()

    if agent_id:
        entries = [e for e in entries if e.get("agent_id") == agent_id]

    if query:
        filtered = []
        for e in entries:
            md_path = _SESSIONS_DIR / e.get("file_path", "")
            if md_path.exists():
                content = md_path.read_text(encoding="utf-8", errors="replace")
                if query.lower() in content.lower():
                    filtered.append(e)
        entries = filtered

    entries.sort(key=lambda x: x.get("start_time", ""), reverse=True)
    entries = entries[:count]

    return {"status": "success", "count": len(entries), "sessions": entries}


def _action_summary(args: dict) -> dict:
    session_id = args.get("session_id", "")
    if not session_id:
        return {"status": "error", "message": "session_id requis pour action=summary"}

    if not _INDEX_PATH.exists():
        return {"status": "error", "message": "sessions_index.json introuvable"}

    entries = _load_index()
    entry = None
    for e in entries:
        if e.get("session_id") == session_id:
            entry = e
            break

    if entry is None:
        return {"status": "error", "message": f"Session {session_id} introuvable"}

    md_path = _SESSIONS_DIR / entry.get("file_path", "")
    content = ""
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8", errors="replace")

    resume = "[Aucun résumé]"
    if "## _session_resume" in content:
        parts = content.split("## _session_resume", 1)
        if len(parts) > 1:
            r = parts[1].strip()[:2000]
            if r:
                resume = r

    return {
        "status": "success",
        "session": {
            "session_id": entry.get("session_id", ""),
            "agent_id": entry.get("agent_id", ""),
            "start_time": entry.get("start_time", ""),
            "end_time": entry.get("end_time", ""),
            "status": entry.get("status", ""),
            "message_count": entry.get("message_count", 0),
            "resume": resume,
            "file_path": entry.get("file_path", ""),
        },
    }


_ACTIONS = {
    "search": _action_search,
    "summary": _action_summary,
}


def run(json_args: str = "{}"):
    try:
        args = json.loads(json_args)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"JSON invalide: {e}"}))
        return
    action = args.get("action", "search")
    if action not in _ACTIONS:
        print(json.dumps({
            "status": "error",
            "message": f"Action inconnue: '{action}'. Actions: {list(_ACTIONS.keys())}",
        }))
        return
    try:
        result = _ACTIONS[action](args)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")