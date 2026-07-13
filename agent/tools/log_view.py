"""log_view.py — Outil agent : lecture SEULE des logs (fichiers plats .log JSONL).

v21: Lit 06_LOGS/*.log au lieu de master_logs.db.

Args JSON: {"count": 20} ou {"agent_id": "agent1", "count": 20}
"""
import sys
import json
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

_LOGS_DIR = Path(__file__).resolve().parents[2] / "06_LOGS"


class LogArgs(BaseModel):
    count: int = Field(20, description="Nombre de logs à retourner (max 100)")
    agent_id: str | None = Field(None, description="Filtrer par agent_id (ex: agent1)")

    @field_validator("count")
    @classmethod
    def validate_count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("count doit être >= 1")
        if v > 100:
            raise ValueError("count max 100")
        return v


def run(json_args: str):
    try:
        args = LogArgs.model_validate_json(json_args)
        if not _LOGS_DIR.exists():
            print(json.dumps({"status": "error", "message": f"Logs dir introuvable: {_LOGS_DIR}"}))
            return

        entries = []
        if args.agent_id:
            log_files = [_LOGS_DIR / f"{args.agent_id}.log"]
        else:
            log_files = sorted(_LOGS_DIR.glob("*.log"))

        for log_file in log_files:
            if not log_file.exists():
                continue
            agent_id = log_file.stem
            for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entry["agent_id"] = agent_id
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        entries = entries[:args.count]

        print(json.dumps({"status": "success", "count": len(entries), "data": entries},
                         ensure_ascii=False, default=str))
    except (ValueError, KeyError, RuntimeError, TypeError, json.JSONDecodeError, OSError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")