"""Client LLM unifié — LiteLLM + Instructor + Tenacity.

Une seule brique d'inférence pour tous les agents.
- LiteLLM route vers le bon provider (DeepSeek, llama.cpp, cloud).
- Instructor force le JSON structuré validé par Pydantic v2.
- Tenacity gère les retries réseau (instructor gère les retries parsing).

R12: LiteLLM = hub unique. Pas de httpx brut. Pas de from scratch.
R1:  Zéro from scratch — litellm fait déjà le travail de 100+ providers.
R5:  Hard crash si tout échoue. Pas de masquage d'erreurs.

Corrections review DeepSeek:
- C1: streaming accumulate chunks dans une liste (pas yield — tenacity retry couvre tout)
- C3: api_key ne passe plus "dummy" — crash net si manquant pour endpoints qui en requièrent un
- Pas d'Ollama (machine cloud) — endpoint "cloud" remplace "ollama"
"""

from typing import Any, Literal
import json

import litellm
from litellm import acompletion, completion
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# --- Types ---

EndpointType = Literal[
    "cloud", "deepseek_api", "llamacpp", "cloud_free", "cloud_paid", "omniroute",
    "openrouter", "gemini", "nvidia", "mistral", "xai", "groq",
]

# Modèles OpenRouter gratuits (curés, capables outils/JSON) — ordre = chaîne de fallback.
# OpenRouter route automatiquement au suivant en cas d'erreur/429 (param `models`).
OPENROUTER_FREE_MODELS = [
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "qwen/qwen3-coder:free",
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-20b:free",
    "google/gemma-4-26b-a4b-it:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
]


# --- Retry réseau via tenacity ---

_NETWORK_ERRORS = (
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.Timeout,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.RateLimitError,
)

_RETRY_DECORATOR = retry(
    retry=retry_if_exception_type(_NETWORK_ERRORS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


# --- Mapping endpoint -> litellm model prefix ---

def _build_litellm_params(
    model: str,
    endpoint: EndpointType,
    llamacpp_url: str = "http://localhost:8088",
    api_key: str | None = None,
) -> dict[str, Any]:
    """Mape un endpoint de config.yaml vers les params litellm.

    R5: Crash net si api_key manquant pour un endpoint qui en requiert un.
    C3: Plus de api_key or "dummy" — si pas de clé, on ne la passe pas.

    Source: litellm docs —
      deepseek/ → model='deepseek/{model}', api_key requis
      openai/   → model='openai/{model}', api_base=llamacpp_url (OpenAI-compatible)
      cloud     → model doit déjà contenir le prefix litellm (ex: 'anthropic/claude-3')
    """
    match endpoint:
        case "cloud":
            # Cloud générique = Ollama Cloud (clé ollama_api_key, cf _get_api_key).
            # OpenAI-compatible: préfixe openai/ + api_base ollama.com/v1.
            # model = tag Ollama Cloud (ex: "glm-4.6", "gpt-oss:120b").
            if not api_key:
                raise ValueError("cloud (Ollama Cloud) requiert un api_key")
            return {
                "model": f"openai/{model}",
                "api_base": "https://ollama.com/v1",
                "api_key": api_key,
            }
        case "deepseek_api":
            if not api_key:
                raise ValueError("deepseek_api requiert un api_key")
            return {
                "model": f"deepseek/{model}",
                "api_key": api_key,
            }
        case "llamacpp":
            # llama.cpp local — OpenAI-compatible. litellm/openai exige une clé même
            # si llama.cpp n'en vérifie aucune → placeholder (sinon "Missing credentials").
            return {
                "model": f"openai/{model}",
                "api_base": f"{llamacpp_url}/v1",
                "api_key": api_key or "llama-cpp-local",
            }
        case "cloud_free" | "cloud_paid":
            if not api_key:
                raise ValueError(f"{endpoint} requiert un api_key")
            return {
                "model": model,
                "api_key": api_key,
            }
        case "omniroute":
            # OmniRoute gateway local — OpenAI-compatible, smart routing auto-fallback
            # model="auto" = zero-config smart routing, ou un provider/model spécifique
            # litellm/openai exige une clé même si OmniRoute gère l'auth en interne
            # (mode free) → placeholder si vide, sinon "Missing credentials".
            return {
                "model": f"openai/{model}",
                "api_base": "http://localhost:20128/v1",
                "api_key": api_key or "omniroute-local",
            }
        case "openrouter":
            # OpenRouter — une clé, modèles *:free. Routage NATIF avec fallback auto
            # sur 429/erreur via extra_body.models (OpenRouter essaie la liste en ordre).
            if not api_key:
                raise ValueError("openrouter requiert un api_key (openrouter_api_key)")
            fallback = [m for m in OPENROUTER_FREE_MODELS if m != model]
            return {
                "model": f"openrouter/{model}",
                "api_key": api_key,
                # OpenRouter limite `models` à 3. litellm ajoute déjà le modèle primaire,
                # donc on ne met QUE 2 alternatives ici (total routé = 3).
                "extra_body": {"models": fallback[:2]},
            }
        case "gemini":
            # Google Gemini (AI Studio, free tier) via litellm — clé directe.
            if not api_key:
                raise ValueError("gemini requiert un api_key (gemini_api_key)")
            return {
                "model": f"gemini/{model}",
                "api_key": api_key,
            }
        case "nvidia":
            # NVIDIA NIM — OpenAI-compatible via integrate.api.nvidia.com
            if not api_key:
                raise ValueError("nvidia requiert un api_key")
            return {
                "model": f"openai/{model}",
                "api_base": "https://integrate.api.nvidia.com/v1",
                "api_key": api_key,
            }
        case "mistral":
            # Mistral API — OpenAI-compatible
            if not api_key:
                raise ValueError("mistral requiert un api_key")
            return {
                "model": f"openai/{model}",
                "api_base": "https://api.mistral.ai/v1",
                "api_key": api_key,
            }
        case "xai":
            # xAI (Grok) — OpenAI-compatible
            if not api_key:
                raise ValueError("xai requiert un api_key")
            return {
                "model": f"openai/{model}",
                "api_base": "https://api.x.ai/v1",
                "api_key": api_key,
            }
        case "groq":
            # Groq — ultra-fast inference, OpenAI-compatible
            if not api_key:
                raise ValueError("groq requiert un api_key")
            return {
                "model": f"openai/{model}",
                "api_base": "https://api.groq.com/openai/v1",
                "api_key": api_key,
            }
        case _:
            raise ValueError(f"Endpoint inconnu: {endpoint}")


# --- Appel simple (texte brut) ---

def call_llm(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    *,
    llamacpp_url: str = "http://localhost:8088",
    api_key: str | None = None,
    timeout: int = 120,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    stream: bool = False,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> str | list[str] | dict:
    """Appelle le LLM et retourne le texte brut ou un dict structuré.

    tools != None → retourne un dict {content, tool_calls, finish_reason}.
    stream=True → retourne une liste de chunks texte (C1: accumulateur, pas yield).
    stream=False → retourne le texte complet ou le dict structuré.
    """
    if stream:
        return _call_llm_stream(
            messages, model, endpoint,
            llamacpp_url=llamacpp_url,
            api_key=api_key, timeout=timeout,
            temperature=temperature, max_tokens=max_tokens,
        )
    return _call_llm_sync(
        messages, model, endpoint,
        llamacpp_url=llamacpp_url,
        api_key=api_key, timeout=timeout,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, tool_choice=tool_choice,
    )


@_RETRY_DECORATOR
def _call_llm_sync(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    **kwargs,
) -> str | dict:
    """Appel synchrone — retry via tenacity couvre toute l'exécution.

    Si tools est passé → retourne un dict {content, tool_calls, finish_reason}.
    Sinon → retourne le texte brut (compatibilité ascendante).
    """
    params = _build_litellm_params(
        model, endpoint,
        llamacpp_url=kwargs.pop("llamacpp_url", "http://localhost:8088"),
        api_key=kwargs.pop("api_key", None),
    )
    tools = kwargs.pop("tools", None)
    tool_choice = kwargs.pop("tool_choice", None)

    extra = {}
    if tools:
        extra["tools"] = tools
    if tool_choice:
        extra["tool_choice"] = tool_choice

    response = completion(
        messages=messages,
        timeout=kwargs.pop("timeout", 120),
        temperature=kwargs.pop("temperature", 0.7),
        stream=False,
        max_tokens=kwargs.pop("max_tokens", None),
        **extra,
        **params,
    )
    msg = response.choices[0].message
    finish_reason = response.choices[0].finish_reason

    # deepseek-reasoner: content peut etre vide, le texte est dans reasoning_content
    text = msg.content
    if not text and hasattr(msg, "reasoning_content") and msg.reasoning_content:
        text = msg.reasoning_content
    text = text or ""

    # Mode tool calling → retourner dict structuré
    if tools is not None:
        tool_calls_raw = getattr(msg, "tool_calls", None) or []
        tool_calls = []
        for tc in tool_calls_raw:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except (json.JSONDecodeError, AttributeError):
                args = {}
            tool_calls.append({
                "id": getattr(tc, "id", ""),
                "name": tc.function.name,
                "arguments": args,
            })
        return {
            "content": text,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
        }

    return text  # jamais None : sinon .strip() plante le parser en aval


@_RETRY_DECORATOR
def _call_llm_stream(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    **kwargs,
) -> list[str]:
    """Appel streaming — C1: accumulate chunks dans une liste.

    Le générateur est consommé DANS la fonction décorée par @retry.
    Les erreurs mid-stream déclenchent le retry (tenacity couvre tout).
    Retourne une liste de chunks (pas un yield) pour que @retry fonctionne.
    """
    params = _build_litellm_params(
        model, endpoint,
        llamacpp_url=kwargs.pop("llamacpp_url", "http://localhost:8088"),
        api_key=kwargs.pop("api_key", None),
    )
    response = completion(
        messages=messages,
        timeout=kwargs.pop("timeout", 120),
        temperature=kwargs.pop("temperature", 0.7),
        stream=True,
        max_tokens=kwargs.pop("max_tokens", None),
        **params,
    )
    # C1: accumuler dans une liste — si erreur mid-stream, tenacity retry tout
    chunks: list[str] = []
    reasoning: list[str] = []
    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            chunks.append(delta.content)
        elif getattr(delta, "reasoning_content", None):
            reasoning.append(delta.reasoning_content)
    return chunks or reasoning


async def call_llm_async(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    *,
    llamacpp_url: str = "http://localhost:8088",
    api_key: str | None = None,
    timeout: int = 120,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    stream: bool = False,
) -> str | list[str]:
    """Version async de call_llm. C1: streaming retourne liste."""
    if stream:
        return await _call_llm_async_stream(
            messages, model, endpoint,
            llamacpp_url=llamacpp_url,
            api_key=api_key, timeout=timeout,
            temperature=temperature, max_tokens=max_tokens,
        )
    return await _call_llm_async_sync(
        messages, model, endpoint,
        llamacpp_url=llamacpp_url,
        api_key=api_key, timeout=timeout,
        temperature=temperature, max_tokens=max_tokens,
    )


@_RETRY_DECORATOR
async def _call_llm_async_sync(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    **kwargs,
) -> str:
    """Appel async — retry via tenacity."""
    params = _build_litellm_params(
        model, endpoint,
        llamacpp_url=kwargs.pop("llamacpp_url", "http://localhost:8088"),
        api_key=kwargs.pop("api_key", None),
    )
    response = await acompletion(
        messages=messages,
        timeout=kwargs.pop("timeout", 120),
        temperature=kwargs.pop("temperature", 0.7),
        stream=False,
        max_tokens=kwargs.pop("max_tokens", None),
        **params,
    )
    msg = response.choices[0].message
    text = msg.content
    if not text and getattr(msg, "reasoning_content", None):
        text = msg.reasoning_content
    return text or ""  # jamais None : sinon .strip() plante le parser en aval


@_RETRY_DECORATOR
async def _call_llm_async_stream(
    messages: list[dict[str, str]],
    model: str,
    endpoint: EndpointType,
    **kwargs,
) -> list[str]:
    """Appel async streaming — C1: accumulate chunks (pas yield)."""
    params = _build_litellm_params(
        model, endpoint,
        llamacpp_url=kwargs.pop("llamacpp_url", "http://localhost:8088"),
        api_key=kwargs.pop("api_key", None),
    )
    response = await acompletion(
        messages=messages,
        timeout=kwargs.pop("timeout", 120),
        temperature=kwargs.pop("temperature", 0.7),
        stream=True,
        max_tokens=kwargs.pop("max_tokens", None),
        **params,
    )
    chunks: list[str] = []
    reasoning: list[str] = []
    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            chunks.append(delta.content)
        elif getattr(delta, "reasoning_content", None):
            reasoning.append(delta.reasoning_content)
    return chunks or reasoning