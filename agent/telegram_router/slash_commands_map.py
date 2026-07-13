"""Mappe les slash commands Telegram vers les clés Redis des agents.

R19: Redis remplace ZMQ — slash command -> agent_key (pas port).
R8: Agent unique paramétré — les clés Redis viennent des configs YAML.

Usage:
    from slash_commands_map import resolve_agent, extract_prompt, is_internal_command
    agent_key = resolve_agent("/main")  # -> "agent1"
"""

# Map statique: slash command -> agent_key (Redis key)
SLASH_MAP: dict[str, str] = {
    "/main": "agent1",       # Agent 01 — glm-5.2
    "/bis": "agent2",        # Agent 02 — gemma-431b
    "/ds": "agent3",         # Agent 03 — deepseek-reasoner
    "/local": "agentlocal",  # Agent 04 — llama-cpp-local
    "/free": "subagent1",    # Agent 05 — qwen-free
    "/paid": "subagent2",    # Agent 06 — TBD
}

# Agent par défaut
DEFAULT_AGENT = "agent1"

# --- Métadonnées pour les claviers inline (bulles de choix) ---
# Ordre = ordre d'affichage des boutons.
AGENTS_TG = [
    {"key": "agent1",     "label": "🟢 Ollama 1 (GLM)",   "endpoint": "cloud"},
    {"key": "agent2",     "label": "🟢 Ollama 2 (Gemma)", "endpoint": "cloud"},
    {"key": "agent3",     "label": "🔵 DS API (DeepSeek)","endpoint": "deepseek_api"},
    {"key": "agentlocal", "label": "💻 Agent Local",       "endpoint": "llamacpp"},
    {"key": "subagent1",  "label": "🆓 Sub1 (OpenRouter)", "endpoint": "openrouter"},
    {"key": "subagent2",  "label": "💳 Sub2",              "endpoint": "cloud_paid"},
]
AGENT_BY_KEY = {a["key"]: a for a in AGENTS_TG}

# Modèles proposés par endpoint (callback_data utilise un INDEX car limite 64 octets)
MODELS_TG = {
    "cloud": ["glm-5.2", "gemma4:31b", "deepseek-v3.2", "qwen3-coder:480b"],
    "cloud_paid": ["glm-5.2", "deepseek-v3.2", "kimi-k2"],
    "deepseek_api": ["deepseek-chat", "deepseek-reasoner"],
    "openrouter": ["openai/gpt-oss-120b:free", "qwen/qwen3-next-80b-a3b-instruct:free",
                   "meta-llama/llama-3.3-70b-instruct:free", "nousresearch/hermes-3-llama-3.1-405b:free"],
    "llamacpp": ["llama-cpp-local"],
}

# Commands spéciales (pas routées vers un agent)
INTERNAL_COMMANDS = {"/status", "/stop", "/kill"}


def resolve_agent(text: str) -> str | None:
    """Résout l'agent_key depuis le texte du message.

    Args:
        text: message Telegram (ex: "/main fais ceci" ou juste "fais ceci")

    Returns:
        agent_key (str) si un agent est ciblé, None si commande interne ou inconnue.
    """
    if not text:
        return DEFAULT_AGENT

    first_word = text.strip().split()[0].lower()

    if first_word in SLASH_MAP:
        return SLASH_MAP[first_word]

    if first_word in INTERNAL_COMMANDS:
        return None

    # Pas de slash command -> agent par défaut
    return DEFAULT_AGENT


def extract_prompt(text: str) -> str:
    """Extrait le prompt utilisateur (sans la slash command).

    "/main fais ceci" -> "fais ceci"
    "salut"           -> "salut"   (texte simple = tout le texte, même 1 mot)
    """
    if not text:
        return ""

    parts = text.strip().split(maxsplit=1)
    first = parts[0].lower()
    if first in SLASH_MAP or first in INTERNAL_COMMANDS:
        return parts[1] if len(parts) > 1 else ""

    # Texte simple (pas de slash) -> le message entier
    return text.strip()


def is_internal_command(text: str) -> bool:
    """Vérifie si le message est une commande interne (/status)."""
    if not text:
        return False
    return text.strip().split()[0].lower() in INTERNAL_COMMANDS