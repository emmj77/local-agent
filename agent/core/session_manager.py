"""Session manager — gère le cycle de vie des sessions via fichiers plats .md.

v21: Plus de SQLite. Les sessions sont des .md dans 04_SESSIONS/.
Un index léger (sessions_index.json) remplace sessions.db pour les métadonnées.

R9: Un fichier = une fonction métier.
R18: Chemins centralisés via config (InstanceConfig).
R23: SessionManager obligatoire dans AgentLoop.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from filelock import FileLock

_log = logging.getLogger(__name__)

RESUME_THRESHOLD = 20


class SessionManager:
    """Gère le cycle de vie d'une session et synchronise context.json."""

    def __init__(self, config):
        self.config = config
        self.agent_id = config.agent.agent_id
        self.vault_root = Path(config.paths.vault_root)
        self.session_dir = self.vault_root / "04_SESSIONS"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.session_dir / "sessions_index.json"
        self.context_json = Path(config.paths.context_json)

        self._current_session = None
        self._session_id = None
        self._message_count = 0
        self.max_history_messages = getattr(
            config.agent, "max_history_messages", 50
        )
        self._start_or_resume_session()

    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self, entries: list[dict]) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(self.index_path))

    def _start_or_resume_session(self):
        entries = self._load_index()
        now = datetime.now().isoformat()
        for e in entries:
            if e.get("agent_id") == self.agent_id and e.get("status") == "active":
                e["status"] = "ended"
                e["end_time"] = now
        self._save_index(entries)

        self._session_id = f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        start_time = datetime.now().isoformat()
        file_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.agent_id}.md"
        self._current_session = self.session_dir / file_name

        self._current_session.write_text(
            self._fallback_template(self._session_id, start_time), encoding="utf-8"
        )

        entries.append({
            "session_id": self._session_id,
            "agent_id": self.agent_id,
            "file_path": self._current_session.name,
            "start_time": start_time,
            "end_time": "",
            "status": "active",
            "message_count": 0,
        })
        self._save_index(entries)

        if self.context_json.exists():
            tmp = self.context_json.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"session_id": "", "session_resume": "", "messages": []},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(self.context_json))

    def append_message(self, role: str, content: str):
        if not self._current_session:
            return

        lock_path = self._current_session.with_suffix(".lock")
        with FileLock(str(lock_path)):
            text = self._current_session.read_text(encoding="utf-8")
            marker = "## Messages"

            formatted_msg = f"\n\n### {role.capitalize()}:\n{content}"
            if marker in text:
                head, tail = text.split(marker, 1)
                text = head + marker + formatted_msg + tail
            else:
                text += formatted_msg

            self._message_count += 1

            if text.startswith("---"):
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if line.startswith("message_count:"):
                        lines[i] = f"message_count: {self._message_count}"
                        break
                text = "\n".join(lines)

            self._current_session.write_text(text, encoding="utf-8")

        entries = self._load_index()
        for e in entries:
            if e.get("session_id") == self._session_id:
                e["message_count"] = self._message_count
                break
        self._save_index(entries)

        self._sync_to_context_json(role, content)

    def _sync_to_context_json(self, role: str, content: str):
        lock = FileLock(str(self.context_json) + ".lock")
        with lock:
            messages = []
            existing_resume = ""
            if self.context_json.exists():
                try:
                    data = json.loads(self.context_json.read_text(encoding="utf-8"))
                    messages = data.get("messages", [])
                    existing_resume = data.get("session_resume", "")
                except Exception as e:
                    _log.warning("context.json illisible (%s) — réinitialisé", e)

            messages.append({"role": role, "content": content})

            overflow_count = len(messages) - self.max_history_messages
            if overflow_count > 0:
                evicted = messages[:overflow_count]
                messages = messages[overflow_count:]

                compact = []
                for m in evicted:
                    role_short = "U" if m["role"] == "user" else "A"
                    c = m["content"][:300]
                    if len(m["content"]) > 300:
                        c += "..."
                    compact.append(f"[{role_short}] {c}")

                resume_parts = [existing_resume] if existing_resume else []
                resume_parts.append("\n".join(compact))
                full_resume = "\n".join(resume_parts)

                if len(full_resume) > 4000:
                    full_resume = "...(tronqué)\n" + full_resume[-4000:]
                session_resume = full_resume
            else:
                session_resume = existing_resume

            ctx_data = {
                "session_id": self._current_session.stem if self._current_session else "",
                "session_resume": session_resume,
                "messages": messages,
            }
            tmp_path = self.context_json.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(ctx_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(str(tmp_path), str(self.context_json))

    @property
    def current_session_path(self) -> Path | None:
        return self._current_session

    def _fallback_template(self, session_id: str, start_time: str) -> str:
        return (
            f'---\nsession_id: "{session_id}"\nagent_id: "{self.agent_id}"\n'
            f'start_time: "{start_time}"\nend_time: ""\nstatus: "active"\n'
            f'message_count: 0\n---\n\n'
            f'# Session {session_id} — {self.agent_id}\n\n'
            f'## Messages\n\n## _session_resume\n\n'
        )