"""dashboard.py — entry point Streamlit + layout. ~70 lignes.

Imports les modules: styles, config_agents, pages, chat.
Lance la page + les onglets agents. C'est tout.
"""
import sys
import time
from pathlib import Path

import streamlit as st

_AGENT_DIR = Path(__file__).resolve().parent.parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

import gpu_manager as gpum

# dashboard/ est un package — on l'ajoute au path pour les imports internes
_DASH_DIR = Path(__file__).resolve().parent
if str(_DASH_DIR) not in sys.path:
    sys.path.insert(0, str(_DASH_DIR))

from styles import CSS
from config_agents import (
    AGENTS, _redis, _redis_ok,
    ENDPOINT_LABEL, WORKING_GGUF,
)
from pages import PAGES
from chat import render_chat, _model_selector

st.set_page_config(page_title="Local Agent - Dashboard", page_icon="🤖", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

# ══════════════════════ LAYOUT ══════════════════════
with st.sidebar:
    st.title("🤖 Local Agent")
    st.markdown(
        f"<span class='la-badge'><span class='la-dot {'up' if _redis_ok else 'down'}'></span>"
        f"Redis {'actif' if _redis_ok else 'down'}</span>", unsafe_allow_html=True)
    for _name in PAGES:
        if st.button(_name, key=f"nav_{_name}", use_container_width=True,
                     type="primary" if st.session_state.page == _name else "secondary"):
            st.session_state.page = _name
            st.rerun()

left, right = st.columns([0.55, 1.35], gap="large")
with left:
    st.subheader(f"📂 {st.session_state.page}")
    with st.container(height=760):
        PAGES[st.session_state.page]()
with right:
    st.subheader("💬 Agents")
    tabs = st.tabs([a["label"] for a in AGENTS])
    for tab, a in zip(tabs, AGENTS):
        with tab:
            ep = a["endpoint"]
            st.markdown(
                f"<span class='la-badge'>🔌 <b>{ENDPOINT_LABEL.get(ep, ep)}</b></span>"
                f"<span class='la-badge'>agent:{a['key']}:queue</span>",
                unsafe_allow_html=True)
            c_m, c_t = st.columns([3, 1])
            with c_m:
                _model_selector(a)
            with c_t:
                tkey = f"agent:{a['key']}:thinking"
                _on = bool(_redis.get(tkey)) if _redis_ok else False
                _nw = st.toggle("🧠", value=_on, key=f"think_{a['id']}",
                                disabled=not _redis_ok, help="Mode réflexion")
                if _redis_ok and _nw != _on:
                    _redis.set(tkey, "1") if _nw else _redis.delete(tkey)
            # --- Contrôle llama.cpp local (onglet agentlocal uniquement) ---
            if a["endpoint"] == "llamacpp":
                ov_model = (_redis.get(f"agent:{a['key']}:model_override") if _redis_ok else None) or a["model"]
                ggufs = WORKING_GGUF
                sel_model = ov_model if ov_model in ggufs else (ggufs[0] if ggufs else None)
                up = gpum.llama_running()
                c1b, c2b = st.columns(2)
                if c1b.button("▶️ Démarrer", key=f"llama_start_{a['id']}", use_container_width=True,
                              disabled=not sel_model or up):
                    with st.spinner(f"Démarrage llama.cpp :8088 ({sel_model})…"):
                        ok = gpum.start_llama(sel_model)
                    if ok:
                        st.success(f"llama.cpp :8088 lancé ({sel_model})")
                    else:
                        st.error("Échec démarrage (binaire absent, modèle introuvable ou VRAM insuffisante).")
                if c2b.button("⏹️ Arrêter", key=f"llama_stop_{a['id']}", use_container_width=True, disabled=not up):
                    gpum.stop_llama()
                    time.sleep(1)
                    vf = gpum.gpu_free_mib(0)
                    st.success(f"llama.cpp :8088 arrêté · GPU0 libre {vf} MiB")
                st.caption(f"{'🟢 up' if up else '🟠 off'} :8088 · modèle: {sel_model or '—'}")
            render_chat(a)