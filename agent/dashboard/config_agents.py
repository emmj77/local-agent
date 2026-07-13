"""config_agents.py — chargement GlobalConfig, AGENTS, Redis, session_state.

Tout ce qui est chargé au démarrage du dashboard. Debug de config isolé ici.
"""
import sys
import json
from pathlib import Path

import streamlit as st
import redis
try:
    import yaml
except Exception:
    yaml = None

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

from config import GlobalConfig

# --- Chemins des sections (données réelles) ---
ROOT = Path("/home/moussa/Applications/Local_Agent")
P = {
    "skills":   ROOT / "03_SKILLS",
    "sessions": ROOT / "04_SESSIONS",
    "memory":   ROOT / "05_MEMORY",
    "logs":     ROOT / "06_LOGS",
    "cron":     ROOT / "07_CRON",
    "tools":    ROOT / "agent" / "tools",
    "agentlog": ROOT / "agent" / "log",
}

# --- Config agents + Redis ---
try:
    gc = GlobalConfig.load()
    AGENTS = [{"id": a.agent_id, "label": a.name or a.model, "key": a.redis_key,
               "model": a.model, "endpoint": a.endpoint} for a in gc.agents]
    _rh, _rp, _rdb = gc.env.redis_host, gc.env.redis_port, gc.env.redis_db
except Exception as e:
    AGENTS = [
        {"id": "agent1", "label": "GLM 5.2", "key": "agent1", "model": "glm-5.2", "endpoint": "cloud"},
        {"id": "agent2", "label": "Gemma 4 31B", "key": "agent2", "model": "gemma4:31b", "endpoint": "cloud"},
        {"id": "agent3", "label": "DeepSeek", "key": "agent3", "model": "deepseek-reasoner", "endpoint": "deepseek_api"},
        {"id": "agentlocal", "label": "Local", "key": "agentlocal", "model": "llama-cpp-local", "endpoint": "llamacpp"},
        {"id": "subagent1", "label": "Subagent Free", "key": "subagent1", "model": "gemini-2.5-flash-lite", "endpoint": "gemini"},
        {"id": "subagent2", "label": "Subagent Paid", "key": "subagent2", "model": "TBD", "endpoint": "cloud"},
    ]
    _rh, _rp, _rdb = "127.0.0.1", 6379, 0
    st.sidebar.warning(f"⚠️ Config fallback ({e})")

AGENT_MAP = {a["id"]: a for a in AGENTS}

ENDPOINT_LABEL = {
    "cloud": "Ollama Cloud", "cloud_free": "Cloud Free", "cloud_paid": "Cloud Paid",
    "deepseek_api": "DeepSeek API", "llamacpp": "llama.cpp local", "openrouter": "OpenRouter",
    "gemini": "Gemini", "nvidia": "NVIDIA", "mistral": "Mistral", "xai": "xAI", "groq": "Groq",
}
MODEL_CHOICES = {
    "deepseek_api": ["deepseek-chat", "deepseek-reasoner"],
    "cloud": ["glm-5.2", "gemma4:31b", "deepseek-v3.2", "qwen3-coder:480b", "kimi-k2", "minimax-m2"],
    "gemini": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
    "openrouter": ["openai/gpt-oss-120b:free", "nvidia/nemotron-3-super-120b-a12b:free",
                   "qwen/qwen3-next-80b-a3b-instruct:free", "meta-llama/llama-3.3-70b-instruct:free",
                   "nousresearch/hermes-3-llama-3.1-405b:free", "qwen/qwen3-coder:free",
                   "google/gemma-4-31b-it:free", "openai/gpt-oss-20b:free",
                   "google/gemma-4-26b-a4b-it:free",
                   "cognitivecomputations/dolphin-mistral-24b-venice-edition:free"],
    "llamacpp": ["llama-cpp-local"],
}
WORKING_GGUF = [
    "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
    "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
    "Qwen_Qwen3-VL-8B-Instruct-Q5_K_M.gguf",
    "gemma-4-12b-it-Q4_K_M.gguf",
    "Qwen3-8B-Q4_K_M.gguf",
    "Qwen2.5-14B-Instruct-Q4_K_M.gguf",
    "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf",
]
GGUF_DIR = Path("/home/moussa/LLM_GGUF")

@st.cache_resource
def _get_redis():
    """Client Redis mis en cache (évite recréer une connexion TCP à chaque rerun)."""
    return redis.Redis(host=_rh, port=_rp, db=_rdb, decode_responses=True,
                       socket_timeout=3, socket_connect_timeout=3)

_redis = _get_redis()
_redis_ok = True
try:
    _redis.ping()
except redis.ConnectionError:
    _redis_ok = False

# --- État de session (histos par agent = chats persistants) ---
if "histories" not in st.session_state:
    st.session_state.histories = {}
for a in AGENTS:
    st.session_state.histories.setdefault(a["id"], [])
if "page" not in st.session_state:
    st.session_state.page = "Serveurs"
st.session_state.setdefault("awaiting", {})

_LIVE_LOG = P["agentlog"] / "agents_live.log"