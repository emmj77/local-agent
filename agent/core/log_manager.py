"""Log manager — fichier plat .log par agent (format JSONL).

v21: Plus de SQLite. Une ligne JSON par entrée de log.
R18: Chemins centralisés dans config (InstanceConfig).
"""

import json
import os
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field

from security.safe_path import safe_path


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    command: str = Field(..., min_length=1)
    output: str


def _get_log_path(config=None) -> str:
    """Résout le chemin du fichier .log depuis config."""
    if config is not None:
        return str(Path(config.paths.logs_dir) / f"{config.agent.agent_id}.log")
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "06_LOGS", "agent.log"))


def log_local(command: str, output: str, config=None):
    """Écrit une entrée de log en JSONL (une ligne JSON par entrée)."""
    entry = LogEntry(command=command, output=output)
    log_path = str(safe_path(_get_log_path(config)))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")