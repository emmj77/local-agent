"""la_client — Client Redis pour le CLI Local_Agent.

Protocole identique au dashboard Streamlit:
  LPUSH agent:{key}:queue → BRPOP agent:response:{corr_id}
  Tool calls lus depuis agents_live.log (offset octets).
"""
import json
import os
import uuid
from pathlib import Path

import redis

AGENT_DIR = Path(__file__).resolve().parent.parent  # cli/ → agent/
LIVE_LOG = AGENT_DIR / "log" / "agents_live.log"

# Fallback si config_global.yaml injoignable
_REDIS_HOST = os.environ.get("LA_REDIS_HOST", "127.0.0.1")
_REDIS_PORT = int(os.environ.get("LA_REDIS_PORT", "6379"))
_REDIS_DB = int(os.environ.get("LA_REDIS_DB", "0"))

# Découverte agents depuis config_global.yaml
def _load_agents():
    try:
        from config import GlobalConfig
        gc = GlobalConfig.load()
        agents = {}
        for a in gc.agents:
            agents[a.agent_id] = {
                "key": a.redis_key, "label": a.name or a.model,
                "model": a.model, "endpoint": a.endpoint,
            }
        if agents:
            return agents
    except Exception:
        pass
    # Fallback
    return {
            "agent1":     {"key": "agent1",     "label": "GLM 5.2",         "model": "glm-5.2",           "endpoint": "cloud"},
            "agent2":     {"key": "agent2",     "label": "Gemma 4 31B",     "model": "gemma4:31b",         "endpoint": "cloud"},
            "agent3":     {"key": "agent3",     "label": "DeepSeek",        "model": "deepseek-reasoner",  "endpoint": "deepseek_api"},
            "agentlocal": {"key": "agentlocal", "label": "Local",           "model": "llama-cpp-local",    "endpoint": "llamacpp"},
            "subagent1":  {"key": "subagent1",  "label": "Subagent Free",   "model": "gemini-2.5-flash-lite", "endpoint": "gemini"},
            "subagent2":  {"key": "subagent2",  "label": "Subagent Paid",   "model": "TBD",                "endpoint": "cloud"},
        }

AGENTS = _load_agents()
DEFAULT_AGENT = os.environ.get("LA_DEFAULT_AGENT", "agent1")


class LAClient:
    """Client Redis pour le CLI Local_Agent."""

    def __init__(self, host=_REDIS_HOST, port=_REDIS_PORT, db=_REDIS_DB):
        self.redis = redis.Redis(host=host, port=port, db=db,
                                 decode_responses=True,
                                 socket_timeout=5, socket_connect_timeout=3)
        self.redis.ping()  # crash si Redis down

    def send(self, agent_key: str, prompt: str) -> str:
        """Envoie un prompt. Retourne le correlation_id."""
        corr_id = str(uuid.uuid4())
        self.redis.lpush(
            f"agent:{agent_key}:queue",
            json.dumps({"prompt": prompt, "source": "cli", "correlation_id": corr_id}),
        )
        return corr_id

    def wait_response(self, corr_id: str, timeout: int = 120) -> dict:
        """Bloque jusqu'à la réponse. Retourne {text, tool_executed, iterations, agent_id}."""
        key = f"agent:response:{corr_id}"
        result = self.redis.brpop(key, timeout=timeout)
        if result is None:
            return {"text": "⏰ Timeout — l'agent n'a pas répondu.", "error": True}
        return json.loads(result[1])

    def send_stop(self, agent_key: str) -> None:
        """Envoie un signal /stop à l'agent."""
        self.redis.set(f"agent:{agent_key}:stop", "1")
        self.redis.delete(f"agent:{agent_key}:queue")

    def stream_tools(self, agent_id: str, corr_id: str):
        """Générateur: lit le log live pour afficher les tool calls en direct.

        Yields: ("tool", {"tool": nom, "args": "..."}) ou ("done", dict).
        """
        tag = f"[{agent_id}]"
        off = LIVE_LOG.stat().st_size if LIVE_LOG.exists() else 0

        while True:
            # Vérifier réponse finale (non-bloquant)
            r = self.redis.lpop(f"agent:response:{corr_id}")
            if r:
                yield ("done", json.loads(r))
                return

            # Lire nouvelles lignes du log
            if LIVE_LOG.exists():
                try:
                    with open(LIVE_LOG, "r", errors="replace") as f:
                        f.seek(off)
                        new = f.read()
                        off = f.tell()
                except OSError:
                    new = ""
                for ln in new.splitlines():
                    if tag in ln:
                        seg = ln.split(tag, 1)[1].strip()
                        if seg.startswith("$ "):
                            rest = seg[2:].strip()
                            parts = rest.split(" ", 1)
                            tool = parts[0]
                            args = parts[1] if len(parts) > 1 else ""
                            yield ("tool", {"tool": tool, "args": args[:120]})

            # Timeout local (évite de boucler si l'agent est mort)
            import time
            time.sleep(0.5)
