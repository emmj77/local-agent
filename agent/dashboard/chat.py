"""chat.py — chat agent: render_chat, _live_sync, _send, _model_selector, etc.

v22: Affiche think + final_answer comme messages. Stop avec pause.
Lit les events depuis Redis PUBLISH (fallback: .log JSONL).
"""
import sys
import json
import uuid
import time
import subprocess
import html
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_DASH_DIR = Path(__file__).resolve().parent
if str(_DASH_DIR) not in sys.path:
    sys.path.insert(0, str(_DASH_DIR))

from config_agents import (
    AGENTS, AGENT_MAP, _redis, _redis_ok, _LIVE_LOG,
    ENDPOINT_LABEL, MODEL_CHOICES, WORKING_GGUF, GGUF_DIR,
)
from pages import _proc_has


# ══════════════════════ BULLES ══════════════════════

def _tool_line(tool: str, arg: str):
    inner = f"🔧 <b>{tool}</b>" + (f" · <code>{arg}</code>" if arg else "")
    st.markdown(f"<div class='la-tool'>{inner}</div>", unsafe_allow_html=True)


def _think_bubble(content):
    """Affiche une pensée de l'agent (style discret, italique)."""
    lines = [ln for ln in str(content).splitlines() if ln.strip()]
    clean = "\n".join(lines)
    st.markdown(
        f"<div class='la-msg think'>💭 <i>{html.escape(clean)}</i></div>",
        unsafe_allow_html=True)


def _msg_bubble(role, content):
    import markdown as _md
    cls = "user" if role == "user" else "assistant"
    lines = [ln for ln in str(content).splitlines() if ln.strip()]
    clean = "\n".join(lines)
    html_body = _md.markdown(clean, extensions=["fenced_code", "tables"])
    st.markdown(f"<div class='la-msg {cls}'>{html_body}</div>",
                unsafe_allow_html=True)


def _tool_result_line(content):
    """Affiche le timer + compteur d'outils (style discret)."""
    st.markdown(
        f"<div class='la-tool-result'>{html.escape(content)}</div>",
        unsafe_allow_html=True)


def _render_history(agent_id):
    for msg in st.session_state.histories[agent_id]:
        mtype = msg.get("type", "text")
        role = msg.get("role", "assistant")
        content = msg.get("content", "")
        if mtype == "think":
            _think_bubble(content)
        elif mtype == "text" and content:
            _msg_bubble(role, content)
        elif mtype == "tool":
            _tool_line(msg.get("tool", "?"), content)
        elif mtype == "tool_result":
            _tool_result_line(content)


# ══════════════════════ ENVOI / SYNC ══════════════════════

def _send(agent_info, prompt):
    aid, key = agent_info["id"], agent_info["key"]
    st.session_state.histories[aid].append(
        {"role": "user", "type": "text", "content": prompt})
    corr = str(uuid.uuid4())
    off = _LIVE_LOG.stat().st_size if _LIVE_LOG.exists() else 0
    _redis.lpush(f"agent:{key}:queue",
                 json.dumps({"prompt": prompt, "source": "dashboard",
                             "correlation_id": corr}))
    st.session_state.awaiting[aid] = {"corr": corr, "off": off}


def _live_sync(agent_info):
    """Synchronise les messages depuis le .log + Redis réponse finale.

    Parse les lignes de log pour détecter:
    - [Pensée de l'agent] → bulle think
    - $ tool {args} → ligne outil
    - Réponse finale via Redis (clé correlation_id)
    """
    aid, key = agent_info["id"], agent_info["key"]
    aw = st.session_state.awaiting.get(aid)
    if not aw:
        return
    tag = f"[{aid}]"

    # 1) Nouvelles lignes du log live
    if _LIVE_LOG.exists():
        try:
            with open(_LIVE_LOG, "r", errors="replace") as f:
                f.seek(aw["off"])
                new = f.read()
                aw["off"] = f.tell()
        except OSError:
            new = ""
        for ln in new.splitlines():
            if tag not in ln:
                continue
            seg = ln.split(tag, 1)[1].strip()
            # think → bulle pensée
            if seg.startswith("[Pensée de l'agent] :"):
                thought = seg[len("[Pensée de l'agent] :"):].strip()
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "think", "content": thought})
            # final_answer → bulle assistant (le texte final arrive aussi via Redis)
            elif seg.startswith("[Final] :"):
                final_text = seg[len("[Final] :"):].strip()
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "text", "content": final_text})
            # timer + compteur → info discrète
            elif seg.startswith("✓ "):
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "tool_result", "content": seg})
            # tool call
            elif seg.startswith("$ "):
                rest = seg[2:].strip()
                tool = rest.split(" ", 1)[0]
                arg = rest[len(tool):].strip()
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "tool",
                     "tool": tool, "content": arg[:120]})

    # 2) Réponse finale (Redis — non-bloquant)
    if _redis_ok:
        r = _redis.lpop(f"agent:response:{aw['corr']}")
        if r:
            txt = json.loads(r).get("text") or "(réponse vide)"
            # Éviter le doublon si déjà ajouté via le log [Final]
            hist = st.session_state.histories[aid]
            if not (hist and hist[-1].get("type") == "text"
                    and hist[-1].get("content") == txt):
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "text", "content": txt})
            del st.session_state.awaiting[aid]


# ══════════════════════ SÉLECTEUR / COMMANDES ══════════════════════

def _model_selector(a):
    ep = a["endpoint"]
    ovkey = f"agent:{a['key']}:model_override"
    if ep == "llamacpp":
        choices = WORKING_GGUF or [a["model"]]
    else:
        choices = list(MODEL_CHOICES.get(ep, [a["model"]]))
    if a["model"] not in choices:
        choices.insert(0, a["model"])
    current = (_redis.get(ovkey) if _redis_ok else None) or a["model"]
    if current not in choices:
        choices.insert(0, current)
    sel = st.selectbox("Modèle", choices, index=choices.index(current),
                       key=f"model_{a['id']}", disabled=not _redis_ok,
                       help=f"Défaut config : {a['model']}")
    if _redis_ok and sel != current:
        if sel == a["model"]:
            _redis.delete(ovkey)
        else:
            _redis.set(ovkey, sel)


def _chat_command(agent_info, cmd):
    aid, key = agent_info["id"], agent_info["key"]
    if cmd == "/kill":
        subprocess.run(["pkill", "-f", "agent_server.py"])
        msg = "💀 **/kill** — tous les agents tués. Relance: `bash agent/start_LA.sh`."
    elif cmd == "/stop":
        if _redis_ok:
            _redis.set(f"agent:{key}:stop", "1")
            _redis.delete(f"agent:{key}:queue")
        msg = "🛑 **/stop** — file vidée + arrêt signalé. L'agent s'arrête au prochain checkpoint."
    elif cmd == "/pause":
        if _redis_ok:
            _redis.set(f"agent:{key}:pause", "1")
        msg = "⏸️ **/pause** — messages en attente. L'agent finit sa tâche puis attend. /resume pour reprendre."
    elif cmd == "/resume":
        if _redis_ok:
            _redis.delete(f"agent:{key}:pause")
        msg = "▶️ **/resume** — reprise. Les messages en attente seront traités."
    st.session_state.awaiting.pop(aid, None)
    st.session_state.histories[aid].append(
        {"role": "assistant", "type": "text", "content": msg})


def _scroll_bottom(aid):
    st.markdown(f"<div id='la-end-{aid}'></div>", unsafe_allow_html=True)
    components.html(f"""
    <script>
    setTimeout(() => {{
        const doc = window.parent.document;
        const anchor = doc.getElementById('la-end-{aid}');
        if (!anchor) return;
        let p = anchor.parentElement;
        while (p && p.scrollHeight <= p.clientHeight) p = p.parentElement;
        if (p) p.scrollTop = p.scrollHeight;
    }}, 120);
    </script>
    """, height=0)


# ══════════════════════ RENDER CHAT ══════════════════════

def render_chat(agent_info):
    aid = agent_info["id"]
    _live_sync(agent_info)
    busy = aid in st.session_state.awaiting

    @st.fragment(run_every="1s" if busy else None)
    def _chat_view():
        _live_sync(agent_info)
        with st.container(height=560):
            _render_history(aid)
            if aid in st.session_state.awaiting:
                st.caption("💭 l'agent travaille…")
            _scroll_bottom(aid)
        if busy and aid not in st.session_state.awaiting:
            st.rerun()

    _chat_view()
    prompt = st.chat_input(
        f"Message à {agent_info['label']}… (/stop, /pause, /resume, /kill)",
        key=f"in_{aid}")
    if prompt:
        cmd = prompt.strip().lower()
        if cmd in ("/stop", "/kill", "/pause", "/resume"):
            _chat_command(agent_info, cmd)
        elif not _redis_ok:
            st.error("🔴 Redis indisponible. `redis-server --port 6379 --daemonize yes`")
        else:
            # Si en pause, stocker le message sans l'envoyer
            if _redis_ok and _redis.get(f"agent:{agent_info['key']}:pause"):
                st.session_state.histories[aid].append(
                    {"role": "user", "type": "text", "content": prompt})
                st.session_state.histories[aid].append(
                    {"role": "assistant", "type": "text",
                     "content": "⏸️ En pause — message mis en attente. /resume pour reprendre."})
            else:
                _send(agent_info, prompt)
        st.rerun()