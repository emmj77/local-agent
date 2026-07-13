"""Parsing LLM — tool calling natif OpenAI + meta-tool lazy loading.

Le LLM utilise le tool calling natif de l'API OpenAI/compatible.
Ce module lit response.choices[0].message.tool_calls et retourne
un AgentResponse structuré.

Le meta-tool request_tool_definition permet au LLM de découvrir
les schémas d'outils à la demande (lazy loading).

finish_reason="stop" sans tool_calls = fini.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from llm.llm_client import EndpointType, call_llm
from llm.tool_registry import get_registry

logger = logging.getLogger(__name__)


# --- Schéma Pydantic v2 (compatibilité agent_loop) ---

class ToolAction(BaseModel):
    """Action d'outil demandée par le LLM."""
    tool_name: str = Field(..., description="Nom du script à exécuter")
    tool_args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments du script"
    )
    is_meta: bool = Field(
        False, description="True si c'est request_tool_definition (pas un outil réel)"
    )
    is_control: bool = Field(
        False, description="True si c'est think ou final_answer (outil de contrôle)"
    )


class AgentResponse(BaseModel):
    """Réponse complète du LLM: texte pour l'utilisateur + optionnellement un outil."""
    text: str = Field(..., description="Réponse en français pour l'utilisateur")
    action: ToolAction | None = Field(
        None, description="Appel d'outil si nécessaire, sinon null"
    )
    finish: bool = Field(
        False, description="True si le LLM a fini (finish_reason=stop, pas de tool_calls)"
    )


# --- Point d'entrée unique ---

def parse_llm_output(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    *,
    llamacpp_url: str = "http://localhost:8088",
    api_key: str | None = None,
    timeout: int = 120,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> AgentResponse:
    """Appelle le LLM avec tool calling natif et parse la réponse.

    tools = liste de schémas OpenAI (meta-tool + outils chargés).
    Retourne AgentResponse avec:
    - action = ToolAction si le LLM demande un outil
    - finish = True si le LLM a fini (finish_reason=stop sans tool_calls)
    """
    raw = call_llm(
        messages=messages,
        model=model,
        endpoint=endpoint,
        llamacpp_url=llamacpp_url,
        api_key=api_key,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
        tools=tools,
        tool_choice=tool_choice,
    )

    # Si tools=None (mode legacy texte), raw est un str
    if isinstance(raw, str):
        return AgentResponse(text=raw.strip(), finish=True)

    # Mode tool calling : raw est un dict
    if isinstance(raw, dict):
        content = raw.get("content", "").strip()
        tool_calls = raw.get("tool_calls", [])
        finish_reason = raw.get("finish_reason", "stop")

        # Pas de tool_calls → le LLM a fini
        if not tool_calls:
            return AgentResponse(text=content, finish=True)

        # Prendre le premier tool_call
        tc = tool_calls[0]
        tool_name = tc.get("name", "")
        tool_args = tc.get("arguments", {})

        # Meta-tool → on retourne le schéma demandé comme "résultat"
        is_meta = tool_name == "request_tool_definition"
        is_control = tool_name in ("think", "final_answer")

        return AgentResponse(
            text=content,
            action=ToolAction(
                tool_name=tool_name,
                tool_args=tool_args,
                is_meta=is_meta,
                is_control=is_control,
            ),
            finish=False,
        )

    # Fallback inattendu
    return AgentResponse(text=str(raw), finish=True)


def get_meta_tool_schema(tool_names: list[str]) -> dict:
    """Construit le schéma du meta-tool avec l'enum des noms d'outils."""
    return get_registry().build_meta_tool(tool_names)


def get_tool_schema(tool_name: str) -> dict | None:
    """Récupère le schéma d'un outil spécifique depuis le registry."""
    return get_registry().get_schema(tool_name)


def get_control_tools_schema() -> list[dict]:
    """Retourne les schémas des outils de contrôle (think + final_answer)."""
    return get_registry().get_control_tools()