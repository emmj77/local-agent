"""CLI Redis client — communication avec les agents via Redis.

R1: Réutilise bus/redis_delegate.py pour le send (LPUSH + BRPOP).
R19: 1 client Redis partagé par session CLI.
R5: Hard crash si Redis injoignable (pas de masquage).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterator

import redis

# Import path agent/
_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

from config import GlobalConfig
from bus.redis_delegate import delegate, DelegateError


def _get_redis_config() -> GlobalConfig:
    """Charge la config globale pour les paramètres Redis."""
    return GlobalConfig.load()


def _make_redis_client(config: GlobalConfig | None = None) -> redis.Redis:
    """Crée un client Redis depuis la config globale."""
    if config is None:
        config = _get_redis_config()
    return redis.Redis(
        host=config.env.redis_host,
        port=config.env.redis_port,
        db=config.env.redis_db,
        decode_responses=True,
    )


def send_to_agent(
    agent_id: str,
    prompt: str,
    timeout_ms: int = 120000,
    todo: str = "",
    redis_client: redis.Redis | None = None,
) -> dict:
    """Envoie un prompt à un agent via Redis et attend la réponse.

    Args:
        agent_id: ID de l'agent cible (ex: "agent1", "subagent1")
        prompt: Instruction à envoyer
        timeout_ms: Timeout pour la réponse (ms), défaut 120s
        todo: Contexte de tâche optionnel
        redis_client: Client Redis partagé (R19). Si None, crée le sien.

    Returns:
        dict: {"text": str, "tool_executed": str|None, "iterations": int,
               "agent_id": str, "correlation_id": str, "error": bool}

    Raises:
        DelegateError: si l'agent ne répond pas dans le timeout
        redis.ConnectionError: si Redis est injoignable
    """
    owns_redis = False
    if redis_client is None:
        redis_client = _make_redis_client()
        owns_redis = True

    try:
        result = delegate(
            target_redis_key=agent_id,
            prompt=prompt,
            todo=todo,
            redis_client=redis_client,
            timeout_ms=timeout_ms,
        )
        return result
    finally:
        if owns_redis:
            try:
                redis_client.close()
            except Exception:
                pass  # C9 fix: ne pas masquer l'exception originale


def subscribe_events(
    agent_id: str,
    redis_client: redis.Redis | None = None,
) -> Iterator[dict]:
    """Subscribe au channel Pub/Sub des events d'un agent.

    Yield les events au fur et à mesure (tool_start, tool_result, final, stream_chunk).

    Args:
        agent_id: ID de l'agent à surveiller
        redis_client: Client Redis partagé. Si None, crée le sien.

    Yields:
        dict: event parsé {"type": str, ...}
    """
    owns_redis = False
    if redis_client is None:
        redis_client = _make_redis_client()
        owns_redis = True

    channel = f"agent:{agent_id}:events"
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)

    try:
        for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    yield json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
    finally:
        pubsub.unsubscribe(channel)
        pubsub.close()
        if owns_redis:
            redis_client.close()


def get_agent_status(agent_id: str, redis_client: redis.Redis | None = None) -> bool:
    """Vérifie si un agent est probablement en vie.

    On vérifie que Redis est accessible (l'agent écoute sa queue via BRPOP).
    On ne peut pas savoir avec certitude si BRPOP est actif, mais si Redis
    répond, c'est que l'infrastructure est là.

    Returns:
        True si Redis est accessible, False sinon.
    """
    owns_redis = False
    if redis_client is None:
        redis_client = _make_redis_client()
        owns_redis = True

    try:
        redis_client.ping()
        return True
    except redis.ConnectionError:
        return False
    finally:
        if owns_redis:
            redis_client.close()


def list_available_agents(config_dir: Path | None = None) -> list[dict]:
    """Liste les configs d'agents disponibles.

    Returns:
        [{"agent_id": str, "model": str, "endpoint": str, "agent_type": str}, ...]
    """
    from config import InstanceConfig

    if config_dir is None:
        config_dir = _AGENT_DIR / "configs"

    agents = []
    for yml in sorted(config_dir.glob("*.yaml")):
        try:
            cfg = InstanceConfig.load(str(yml))
            agents.append({
                "agent_id": cfg.agent.agent_id,
                "model": cfg.agent.model,
                "endpoint": cfg.agent.endpoint,
                "agent_type": cfg.agent.agent_type,
                "config_file": str(yml),
            })
        except Exception:
            # Skip configs cassées — ne crash pas la liste
            continue

    return agents


def get_agent_logs(agent_id: str, limit: int = 20, config_dir: Path | None = None) -> list[dict]:
    """Récupère les derniers logs d'un agent depuis son fichier .log JSONL.

    Returns:
        [{"timestamp": str, "command": str, "output": str}, ...]
    """
    import json

    if config_dir is None:
        config_dir = _AGENT_DIR / "configs"

    cfg_path = config_dir / f"{agent_id}.yaml"
    if not cfg_path.exists():
        return []

    from config import InstanceConfig
    cfg = InstanceConfig.load(str(cfg_path))
    log_path = Path(cfg.paths.logs_dir) / f"{agent_id}.log"

    if not log_path.exists():
        return []

    entries = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return entries[:limit]