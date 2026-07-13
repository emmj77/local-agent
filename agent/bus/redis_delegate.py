"""RedisDelegate — client LPUSH pour déléguer une tâche à un autre agent.

R8: Agent unique paramétré — delegate vers la queue Redis d'un autre agent.
R19: 1 client Redis par instance (singleton connection_pool).
R5: Hard crash si tout échoue (tenacity reraise=True).
"""

from __future__ import annotations

import uuid

import redis
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class DelegateError(Exception):
    """Agent cible injoignable ou réponse invalide."""


class DelegateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    source: str = Field("delegate")
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    todo: str = Field("")


class DelegateResponse(BaseModel):
    text: str = ""
    tool_executed: str | None = None
    iterations: int = 0
    agent_id: str = ""
    correlation_id: str = ""
    error: bool = False


@retry(
    # On ne retente QUE sur ConnectionError (le LPUSH n'a probablement pas abouti).
    # PAS sur DelegateError/timeout : l'agent cible a déjà reçu la requête et
    # l'exécute — un 2e LPUSH la ferait ré-exécuter (double effet de bord).
    retry=retry_if_exception_type(redis.ConnectionError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def delegate(
    target_redis_key: str,
    prompt: str,
    todo: str = "",
    redis_client: redis.Redis | None = None,
    config=None,
    timeout_ms: int = 120000,
) -> dict:
    """Délègue une tâche à un autre agent via Redis.

    Note: si le BRPOP timeout, le retry renvoie un NOUVEAU LPUSH.
    L'agent cible peut recevoir la requête 2x (idempotency à gérer côté cible).

    Args:
        target_redis_key: Key Redis de l'agent cible (ex: "agentlocal")
        prompt: Instruction à envoyer
        todo: Contexte de tâche optionnel
        redis_client: Client Redis partagé (R19). Si None, crée le sien.
        config: InstanceConfig (pour créer le client si besoin)
        timeout_ms: Timeout pour la réponse (ms)

    Returns:
        dict de réponse de l'agent cible

    Raises:
        DelegateError: si l'agent cible ne répond pas ou répond invalide
    """
    # R19: client Redis partagé ou créé ici
    owns_redis = False
    if redis_client is None:
        if config is None:
            raise ValueError("config ou redis_client requis")
        redis_client = redis.Redis(
            host=config.env.redis_host,
            port=config.env.redis_port,
            db=config.env.redis_db,
            decode_responses=True,
        )
        owns_redis = True

    try:
        request = DelegateRequest(prompt=prompt, todo=todo)
        target_queue = f"agent:{target_redis_key}:queue"
        response_key = f"agent:response:{request.correlation_id}"

        # Vérifier que la queue cible existe (agent doit tourner)
        # Note: on ne peut pas vérifier si BRPOP écoute, mais on peut envoyer

        # Envoyer la requête
        redis_client.lpush(target_queue, request.model_dump_json())

        # Attendre la réponse (BRPOP avec timeout)
        timeout_s = timeout_ms / 1000
        result = redis_client.brpop(response_key, timeout=timeout_s)

        if result is None:
            raise DelegateError(
                f"Agent {target_redis_key} n'a pas répondu en {timeout_ms}ms"
            )

        _, raw_response = result
        try:
            data = DelegateResponse.model_validate_json(raw_response).model_dump()
        except Exception as e:
            raise DelegateError(f"Réponse invalide de {target_redis_key}: {e}")

        if data.get("error"):
            raise DelegateError(f"Agent {target_redis_key} a retourné une erreur: {data.get('text')}")

        return data

    finally:
        if owns_redis:
            try:
                redis_client.close()
            except Exception:
                pass  # C2 fix: ne pas masquer l'exception originale