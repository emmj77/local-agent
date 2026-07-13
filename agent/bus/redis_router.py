"""RedisRouter — serveur BRPOP pour une instance d'agent.

R8: Agent unique paramétré — chaque instance écoute sa propre queue Redis.
R19: 1 client Redis par instance (singleton connection_pool).
R5: Hard crash si Redis injoignable. Pas de masquage.

Usage:
    router = RedisRouter(config, handler=lambda req: loop.run(req["prompt"]))
    router.start()  # bloquant — BRPOP
"""

from __future__ import annotations

import json
import time
import traceback
import uuid

import redis
from pydantic import BaseModel, Field, ValidationError


class RedisRequest(BaseModel):
    """Requête validée par Pydantic v2."""
    prompt: str = Field(..., min_length=1, description="Instruction utilisateur")
    source: str = Field("dashboard", description="Origine: dashboard|telegram|watcher|delegate")
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    todo: str = Field(default="", description="Contexte de tâche optionnel")


class RedisRouter:
    """Serveur Redis — BRPOP sur la queue de cette instance.

    Boucle bloquante: BRPOP -> valider -> handler -> LPUSH réponse.
    """

    def __init__(self, config, handler, redis_client: redis.Redis | None = None):
        """
        Args:
            config: InstanceConfig
            handler: callable(request: dict) -> dict (retourne la réponse)
            redis_client: client Redis partagé (R19 singleton). Si None, crée le sien.
        """
        self.config = config
        self.agent_id = config.agent.agent_id
        self.queue_key = config.redis_queue_key
        self.handler = handler
        self._running = False
        self._reconnect_count = 0

        # R19: client Redis partagé ou créé ici
        if redis_client is not None:
            self.redis = redis_client
            self._owns_redis = False
        else:
            self.redis = redis.Redis(
                host=config.env.redis_host,
                port=config.env.redis_port,
                db=config.env.redis_db,
                decode_responses=True,
            )
            self._owns_redis = True

    def start(self) -> None:
        """Boucle bloquante — BRPOP sur la queue. Arrêt via Ctrl+C ou stop()."""
        self._running = True

        # Vérifier la connexion Redis (R5: crash net si injoignable)
        try:
            self.redis.ping()
        except redis.ConnectionError as e:
            raise RuntimeError(f"[{self.agent_id}] Redis injoignable: {e}")

        print(f"[{self.agent_id}] BRPOP sur {self.queue_key} (model={self.config.agent.model})")

        # NB: on ne réinstalle PAS de handler SIGTERM ici — celui de zombie_killer
        # (install_hooks) fait un os._exit immédiat, ce qui tue l'agent même s'il
        # est bloqué dans un appel LLM. Un handler local qui ne pose qu'un flag
        # serait ignoré pendant un appel réseau long -> zombie.

        while self._running:
            try:
                # BRPOP bloquant avec timeout 5s (pour check _running périodiquement)
                result = self.redis.brpop(self.queue_key, timeout=5)
                self._reconnect_count = 0  # brpop OK -> Redis joignable, reset
                if result is None:
                    # Timeout — pas de message, continuer
                    continue

                # result = (key, value)
                _, raw_msg = result

                # Check pause: si en pause, remettre le message dans la queue
                if self.redis.get(f"agent:{self.agent_id}:pause"):
                    self.redis.lpush(self.queue_key, raw_msg)
                    time.sleep(1)  # Éviter spin
                    continue

                self._process_message(raw_msg)

            except redis.exceptions.TimeoutError:
                # BRPOP timeout socket (pas un message) — reboucler proprement.
                # Sinon l'agent crashe sur inactivité (bug: TimeoutError non gérée).
                continue
            except redis.ConnectionError as e:
                self._reconnect_count += 1
                if self._reconnect_count >= 5:
                    print(f"[{self.agent_id}] Redis injoignable après 5 tentatives — crash")
                    raise
                print(f"[{self.agent_id}] Reconnexion Redis ({self._reconnect_count}/5): {e}")
                time.sleep(2)
                continue
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print(f"[{self.agent_id}] Erreur message: {e}")
                continue
            except KeyboardInterrupt:
                print(f"\n[{self.agent_id}] Arrêt (Ctrl+C)")
                break

        self._cleanup()

    def _process_message(self, raw_msg: str) -> None:
        """Parse, valide, exécute le handler, renvoie la réponse via Redis."""
        try:
            data = json.loads(raw_msg)
            request = RedisRequest(**data)
        except (json.JSONDecodeError, ValidationError) as e:
            # Réponse d'erreur si on a un correlation_id
            try:
                data = json.loads(raw_msg)
                corr_id = data.get("correlation_id", "unknown")
            except (json.JSONDecodeError, ValueError, TypeError):
                corr_id = "unknown"

            error_response = {
                "text": f"[ERREUR] Requête invalide: {e}",
                "tool_executed": None,
                "iterations": 0,
                "agent_id": self.agent_id,
                "correlation_id": corr_id,
                "error": True,
            }
            response_key = f"agent:response:{corr_id}"
            self.redis.lpush(response_key, json.dumps(error_response))
            self.redis.expire(response_key, 300)  # TTL: éviter la fuite si non consommé
            return

        # Exécuter le handler (AgentLoop.run)
        try:
            response = self.handler(request.model_dump())
        except Exception as e:
            # Résilience daemon : une requête qui échoue (erreur LLM, provider, etc.)
            # ne doit PAS tuer l'agent — on renvoie une réponse d'erreur et on continue.
            traceback.print_exc()
            response = {
                "text": f"[ERREUR] {type(e).__name__}: {e}",
                "tool_executed": None,
                "iterations": 0,
                "agent_id": self.agent_id,
                "correlation_id": request.correlation_id,
                "error": True,
            }

        # Le handler DOIT renvoyer un dict — sinon la construction de la réponse
        # plus bas lève TypeError, avalée par start() -> réponse jamais livrée
        # (client bloqué jusqu'au timeout). On normalise en réponse d'erreur.
        if not isinstance(response, dict):
            response = {
                "text": f"[ERREUR] handler a renvoyé {type(response).__name__} au lieu d'un dict",
                "tool_executed": None, "iterations": 0,
                "agent_id": self.agent_id,
                "correlation_id": request.correlation_id, "error": True,
            }

        # Ajouter correlation_id + agent_id si manquant
        if "correlation_id" not in response:
            response["correlation_id"] = request.correlation_id
        if "agent_id" not in response:
            response["agent_id"] = self.agent_id

        # LPUSH la réponse + EXPIRE en pipeline atomique (sinon un crash entre les
        # deux laisse une clé sans TTL = fuite Redis).
        response_key = f"agent:response:{request.correlation_id}"
        pipe = self.redis.pipeline()
        pipe.lpush(response_key, json.dumps(response))
        pipe.expire(response_key, 300)
        pipe.execute()

    def stop(self) -> None:
        """Arrête la boucle BRPOP (usage programmatique)."""
        self._running = False

    def _cleanup(self) -> None:
        """Ferme le client Redis si on le possède."""
        if self._owns_redis:
            self.redis.close()