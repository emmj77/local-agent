"""RedisPublisher — PUBLISH events pour affichage en direct (tools, streaming).

R8: Publie sur le channel Redis de cette instance.
R19: 1 client Redis par instance (singleton connection_pool partagé avec router).
R8b: agent_id dans chaque event.

Events:
    {"type": "tool_start", "tool": "...", "args": {...}, "agent_id": "..."}
    {"type": "tool_result", "tool": "...", "output": "...", "success": true, "agent_id": "..."}
    {"type": "final", "text": "...", "iterations": N, "agent_id": "..."}
"""

from __future__ import annotations

import json

import redis


class RedisPublisher:
    """Publisher Redis — PUBLISH events sur le channel de cette instance."""

    def __init__(self, config, redis_client: redis.Redis | None = None):
        """
        Args:
            config: InstanceConfig
            redis_client: Client Redis partagé (R19). Si None, crée le sien.
        """
        self.config = config
        self.agent_id = config.agent.agent_id
        self.channel = config.redis_events_channel

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

    def _publish(self, event: dict) -> None:
        """Publie un event sur le channel Redis."""
        event["agent_id"] = self.agent_id
        self.redis.publish(self.channel, json.dumps(event))

    def tool_start(self, tool_name: str, tool_args: dict) -> None:
        """Publie un event tool_start."""
        self._publish({
            "type": "tool_start",
            "tool": tool_name,
            "args": tool_args,
        })

    def tool_result(self, tool_name: str, output: str, success: bool = True) -> None:
        """Publie un event tool_result."""
        # Tronquer l'output si trop long (pas de flood Redis)
        truncated = len(output) > 500
        display_output = output[:500] if truncated else output
        self._publish({
            "type": "tool_result",
            "tool": tool_name,
            "output": display_output,
            "success": success,
            "truncated": truncated,
        })

    def final(self, text: str, iterations: int) -> None:
        """Publie un event final."""
        self._publish({
            "type": "final",
            "text": text,
            "iterations": iterations,
        })

    def stream_chunk(self, text: str) -> None:
        """Publie un chunk de streaming."""
        self._publish({
            "type": "stream_chunk",
            "text": text,
        })

    def close(self) -> None:
        """Ferme le client Redis si on le possède."""
        if self._owns_redis:
            self.redis.close()