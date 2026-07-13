"""Task queue Redis — tâches longues en background via Thread.

Flow:
  Agent   → launch_background(tool, args) → LPUSH + Thread.start()
  Thread  → route_tool_call() → LPUSH résultat dans Redis
  Agent   → collect_results() → RPOP tout → injecte dans l'historique

Zéro worker séparé, zéro timeout bloquant. Le LLM reçoit un message
système naturel quand la tâche est terminée.
"""


import json
import threading
import uuid


def launch_background(
    tool_name: str,
    tool_args: dict,
    redis_client,
    config,
    route_fn,
) -> str:
    """Lance une tâche en Thread, pousse le résultat dans Redis.

    Returns:
        task_id (8 premiers caractères UUID)
    """
    task_id = str(uuid.uuid4())[:8]
    result_list = f"agent:{config.agent.redis_key}:bg_results"

    def _run():
        try:
            result = route_fn(tool_name, tool_args, config)
            if not isinstance(result, dict):
                result = {"success": False, "output": f"[résultat non-dict: {result!r}]"}
        except Exception as exc:
            result = {"success": False, "output": str(exc)}
        payload = {
            "task_id": task_id,
            "tool_name": tool_name,
            "output": result.get("output", ""),
            "success": result.get("success", False),
        }
        try:
            # LPUSH + TTL en pipeline : si le résultat n'est jamais collecté
            # (agent redémarré/tué avant collect_results), la liste expire au
            # lieu de fuir en mémoire Redis indéfiniment.
            pipe = redis_client.pipeline()
            pipe.lpush(result_list, json.dumps(payload))
            pipe.expire(result_list, 3600)
            pipe.execute()
        except Exception:
            pass  # Redis indispo : rien à faire dans un thread daemon

    threading.Thread(target=_run, daemon=True).start()
    return task_id


def collect_results(redis_client, config) -> list[dict]:
    """Récupère tous les résultats de tâches background terminées.

    RPOP non-bloquant — vide la liste en une fois.
    """
    result_list = f"agent:{config.agent.redis_key}:bg_results"
    results = []
    while True:
        raw = redis_client.rpop(result_list)
        if raw is None:
            break
        try:
            results.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return results
