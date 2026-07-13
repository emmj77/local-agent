"""logging_config — structlog configuration (R24).

R24: Logs structurés JSON + correlation_id propagé via Redis.

Usage:
    from core.logging_config import get_logger
    logger = get_logger("agent1", correlation_id="uuid-...")
    logger.info("tool_executed", tool="read_file", success=True)
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

try:
    import structlog
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False


def configure_logging(agent_id: str = "local_agent") -> None:
    """Configure structlog au démarrage de l'instance.

    R24: Format JSON avec timestamp, level, agent_id, correlation_id.
    """
    if not _HAS_STRUCTLOG:
        # Fallback: logging standard si structlog pas installé
        logging.basicConfig(
            format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            level=logging.INFO,
            stream=sys.stderr,
        )
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Injecter agent_id dans le contexte global
    structlog.contextvars.bind_contextvars(agent_id=agent_id)


def get_logger(agent_id: str = "local_agent", correlation_id: str | None = None):
    """Retourne un logger structlog avec agent_id + correlation_id.

    Args:
        agent_id: ID de l'instance (ex: "agent1")
        correlation_id: UUID de correlation (propagé via Redis). Si None, génère un nouveau.

    Returns:
        structlog logger ou logging.Logger (fallback)
    """
    if not _HAS_STRUCTLOG:
        logger = logging.getLogger(agent_id)
        return logger

    if correlation_id is not None:
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    return structlog.get_logger(agent_id)


def set_correlation_id(correlation_id: str) -> None:
    """Met à jour le correlation_id (utile quand on reçoit une requête Redis)."""
    if _HAS_STRUCTLOG:
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)