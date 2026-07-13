"""memory_manage — outil unifié de gestion mémoire (add/read/list/search).

v21: Plus de SQLite ni ChromaDB. Fichiers .md plats + index JSON.
Recherche textuelle simple (substring sur les .md).

Actions:
  add     → {"action": "add", "content": "...", "category": "...", "source": "..."}
  read    → {"action": "read", "memory_id": "..."}
  list    → {"action": "list", "limit": 10}
  search  → {"action": "search", "query": "...", "limit": 5}
"""
import sys
import json
import os
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/

_VAULT_ROOT = Path(__file__).resolve().parents[2]
_MEMORY_DIR = _VAULT_ROOT / "05_MEMORY"
_INDEX_PATH = _MEMORY_DIR / "memory_index.json"


def _load_index() -> list[dict]:
    if not _INDEX_PATH.exists():
        return []
    try:
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(entries: list[dict]) -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(_INDEX_PATH))


class MemoryManageArgs(BaseModel):
    action: str = Field("list", description="Action: add, read, list, search")
    content: str = Field("", description="Contenu (action=add)")
    category: str = Field("general", description="Catégorie (action=add)")
    source: str = Field("agent", description="Source (action=add)")
    memory_id: str = Field("", description="ID mémoire (action=read)")
    limit: int = Field(10, description="Limite (action=list/search)")
    query: str = Field("", description="Recherche (action=search)")


def _action_add(args: dict) -> dict:
    from security.safe_path import safe_path
    content = args.get("content", "")
    if not content:
        return {"status": "error", "message": "content requis pour action=add"}
    category = args.get("category", "general")
    source = args.get("source", "agent")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_source = source.replace("/", "_").replace("\\", "_").replace("..", "_")
    filename = f"MEMORY_{timestamp}_{safe_source}.md"
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    md_path = safe_path(str(_MEMORY_DIR / filename))
    iso_ts = datetime.now().isoformat()
    md_content = f"---\ntimestamp: {iso_ts}\ncategory: {category}\nsource: {source}\n---\n\n{content}\n"
    md_path.write_text(md_content, encoding="utf-8")
    entries = _load_index()
    entries.append({"timestamp": iso_ts, "category": category, "source": source, "content": content[:500], "file": filename})
    _save_index(entries)
    return {"status": "success", "file": str(md_path), "index": str(_INDEX_PATH)}


def _action_read(args: dict) -> dict:
    from security.safe_path import guarded_path, PathBlockedError
    memory_id = args.get("memory_id", "")
    if not memory_id:
        return {"status": "error", "message": "memory_id requis pour action=read"}
    candidates = list(_MEMORY_DIR.glob(f"*{memory_id}*.md"))
    if not candidates:
        return {"status": "error", "message": f"Mémoire '{memory_id}' introuvable"}
    try:
        guarded_path(str(candidates[0]))
    except PathBlockedError as e:
        return {"status": "error", "message": str(e)}
    content = candidates[0].read_text(encoding="utf-8")
    return {"status": "success", "file": candidates[0].name, "content": content}


def _action_list(args: dict) -> dict:
    limit = args.get("limit", 10)
    if not _MEMORY_DIR.is_dir():
        return {"status": "success", "count": 0, "memories": []}
    files = sorted((f.name for f in _MEMORY_DIR.iterdir() if f.is_file() and f.name.endswith(".md")), reverse=True)[:limit]
    return {"status": "success", "count": len(files), "memories": files}


def _action_search(args: dict) -> dict:
    query = args.get("query", "")
    limit = args.get("limit", 5)
    if not query:
        return {"status": "error", "message": "query requis pour action=search"}
    if not _MEMORY_DIR.is_dir():
        return {"status": "success", "query": query, "count": 0, "results": []}
    results = []
    query_lower = query.lower()
    for md_file in sorted(_MEMORY_DIR.glob("*.md"), reverse=True):
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
            if query_lower in content.lower():
                category = ""
                source = ""
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 2:
                        for line in parts[1].splitlines():
                            if line.startswith("category:"):
                                category = line.split(":", 1)[1].strip().strip('"')
                            elif line.startswith("source:"):
                                source = line.split(":", 1)[1].strip().strip('"')
                results.append({"file": md_file.name, "content": content[:300], "category": category, "source": source})
                if len(results) >= limit:
                    break
        except OSError:
            continue
    return {"status": "success", "query": query, "count": len(results), "results": results}


_ACTIONS = {
    "add": _action_add,
    "read": _action_read,
    "list": _action_list,
    "search": _action_search,
}


def run(json_args: str = "{}"):
    try:
        args = json.loads(json_args)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"JSON invalide: {e}"}))
        return
    action = args.get("action", "list")
    if action not in _ACTIONS:
        print(json.dumps({"status": "error", "message": f"Action inconnue: '{action}'. Actions: {list(_ACTIONS.keys())}"}))
        return
    try:
        result = _ACTIONS[action](args)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError, ImportError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")