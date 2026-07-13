"""Point d'entrée de l'agent — démarre le serveur Redis branché sur agent_loop.

Agent unique paramétré (R8): lance une instance avec --config.
Le serveur fait BRPOP sur la queue Redis de cette instance.

R19: 1 client Redis partagé (router + publisher + task queue).

Démarrage:
  python agent/agent_server.py --config agent/configs/agent1.yaml
"""


import argparse
import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

import redis
from config import InstanceConfig
from agent_loop import AgentLoop
from bus.redis_router import RedisRouter
from bus.redis_publisher import RedisPublisher
from core.logging_config import configure_logging, set_correlation_id
from security.safe_path import set_vault_root
from security.zombie_killer import install_hooks


def main():
    parser = argparse.ArgumentParser(description="Local_Agent — serveur d'instance")
    parser.add_argument(
        "--config",
        required=True,
        help="Chemin vers le YAML de config (ex: agent/configs/agent1.yaml)",
    )
    args = parser.parse_args()

    # Charger la config d'instance (crash net si invalide — R2)
    config = InstanceConfig.load(args.config)

    # Phase I: Installer les hooks de sécurité (zombie killer, crash backup, etc.)
    install_hooks(config)

    # R19: 1 client Redis partagé pour router + publisher + task queue
    redis_client = redis.Redis(
        host=config.env.redis_host,
        port=config.env.redis_port,
        db=config.env.redis_db,
        decode_responses=True,
    )

    # Vérifier la connexion Redis (R5: crash net si injoignable)
    try:
        redis_client.ping()
    except redis.ConnectionError as e:
        print(f"[{config.agent.agent_id}] ERREUR: Redis injoignable sur "
              f"{config.env.redis_host}:{config.env.redis_port} — {e}")
        sys.exit(1)

    # Vider la queue au démarrage (sinon anciens messages repris)
    redis_client.delete(config.redis_queue_key)

    # RedisPublisher (PUBLISH events)
    publisher = RedisPublisher(config=config, redis_client=redis_client)

    # AgentLoop
    loop = AgentLoop(config=config, publisher=publisher, redis_client=redis_client)

    # Handler: RedisRouter -> AgentLoop.run
    def handler(request: dict) -> dict:
        set_correlation_id(request.get("correlation_id", ""))
        return loop.run(request.get("prompt", ""))

    # RedisRouter (BRPOP serveur)
    router = RedisRouter(
        config=config,
        handler=handler,
        redis_client=redis_client,
    )

    configure_logging(config.agent.agent_id)
    set_vault_root(config.paths.vault_root)

    print(f"[{config.agent.agent_id}] Démarrage Redis queue={config.redis_queue_key} "
          f"events={config.redis_events_channel} (model={config.agent.model})")
    print(f"[{config.agent.agent_id}] {len(loop.tool_names)} outils | "
          f"max_calls={loop.max_tool_calls} | type={config.agent.agent_type}")

    try:
        router.start()
    finally:
        publisher.close()
        redis_client.close()


if __name__ == "__main__":
    main()