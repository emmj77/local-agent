"""pages.py — les 7 pages du dashboard (Serveurs, Skills, Sessions, Mémoire, Cron, Logs, Outils).

Chaque page est une fonction indépendante. Bug sur une page? tu ouvres pages.py.
"""
import os
import sys
import time
import subprocess
import html
import socket
from pathlib import Path

import streamlit as st

# dashboard/ est un package — on l'ajoute au path pour les imports internes
_DASH_DIR = Path(__file__).resolve().parent
if str(_DASH_DIR) not in sys.path:
    sys.path.insert(0, str(_DASH_DIR))

from config_agents import (
    P, AGENTS, AGENT_MAP, _redis, _redis_ok, _LIVE_LOG,
)
from styles import AUTO_REFRESH_JS

try:
    import yaml
except Exception:
    yaml = None


# ══════════════════════ HELPERS ══════════════════════

def _parse_fm(text: str) -> dict:
    if yaml and text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            try:
                return yaml.safe_load(text[3:end]) or {}
            except Exception:
                return {}
    return {}

def _tail(path: Path, n: int = 40) -> str:
    try:
        return "".join(path.read_text(errors="replace").splitlines(keepends=True)[-n:])
    except Exception:
        return ""

def _procs() -> str:
    try:
        return subprocess.run(["pgrep", "-af", "agent_server.py"],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""

def _llama_up() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:8088/health", timeout=1)
        return True
    except Exception:
        return False

def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except Exception:
        return False

def _proc_has(marker):
    try:
        return bool(subprocess.run(["pgrep", "-f", marker], capture_output=True,
                                   text=True, timeout=3).stdout.strip())
    except Exception:
        return False

def _gpu_info():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            gpus.append(parts)
    return gpus

def _list_md(folder: Path, limit: int, recursive: bool = False):
    if not folder.exists():
        return []
    it = folder.rglob("*.md") if recursive else folder.glob("*.md")
    files = [f for f in it if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:limit]


# ══════════════════════ PAGES ══════════════════════

def page_servers():
    st.components.v1.html(AUTO_REFRESH_JS, height=0)
    procs = _procs()
    on = st.toggle("🔄 Refresh auto (10s)", key="svr_refresh")
    if on:
        st.components.v1.html(
            "<div style='display:flex;align-items:center;gap:6px;padding:4px 0;color:var(--muted);font-size:13px'>"
            "Prochain refresh dans "
            "<span id='_ar_display' style='color:var(--blue)'>10s</span>"
            "</div><script>window._ar_start(10)</script>", height=24)
    def dot(up): return f"<span class='la-dot {'up' if up else 'down'}'></span>"
    rows = [f"<div class='la-kv'><span class='k'>{dot(_redis_ok)}Redis :6379</span>"
            f"<span class='v'>{'PONG' if _redis_ok else 'down'}</span></div>"]
    for a in AGENTS:
        up = f"configs/{a['id']}.yaml" in procs
        rows.append(f"<div class='la-kv'><span class='k'>{dot(up)}{a['label']} "
                    f"<small>({a['model']})</small></span>"
                    f"<span class='v'>{'actif' if up else 'arrêté'}</span></div>")
    lc = _llama_up()
    rows.append(f"<div class='la-kv'><span class='k'>{dot(lc)}llama.cpp :8088</span>"
                f"<span class='v'>{'up' if lc else 'off'}</span></div>")
    for label, port in (("ComfyUI :8188", 8188), ("Qwen3-VL vision :1234", 1234), ("obscura MCP :3000", 3000)):
        u = _port_open(port)
        rows.append(f"<div class='la-kv'><span class='k'>{dot(u)}{label}</span>"
                    f"<span class='v'>{'up' if u else 'off'}</span></div>")
    st.markdown(f"<div class='la-card'>{''.join(rows)}</div>", unsafe_allow_html=True)

    gpus = _gpu_info()
    if gpus:
        grows = []
        for name, used, total, temp, util in ((g[0], g[1], g[2], g[3], g[4]) for g in gpus):
            try:
                pct = int(float(used) / float(total) * 100) if float(total) else 0
            except ValueError:
                pct = 0
            grows.append(f"<div class='la-kv'><span class='k'>🎮 {name}</span>"
                         f"<span class='v'>{used}/{total} MiB ({pct}%) · {temp}°C · util {util}%</span></div>")
        st.markdown(f"<div class='la-card'>{''.join(grows)}</div>", unsafe_allow_html=True)


def page_skills():
    root = P["skills"]
    smds = sorted(root.glob("*/SKILL.md")) if root.exists() else []
    aid = st.selectbox("Agent", [a["id"] for a in AGENTS],
                       format_func=lambda x: AGENT_MAP[x]["label"], key="skills_agent")
    rkey = f"agent:{AGENT_MAP[aid]['key']}:skills_disabled"
    disabled = set(_redis.smembers(rkey)) if _redis_ok else set()
    st.caption(f"{len(smds)} skills · {len(disabled)} désactivé(s) · 1 skill = 1 dossier/SKILL.md")
    if not _redis_ok:
        st.warning("Redis down : toggles indisponibles.")
    if not smds:
        st.caption("Aucun skill (03_SKILLS/<nom>/SKILL.md).")
    for smd in smds:
        sid = smd.parent.name
        fm = _parse_fm(smd.read_text(errors="replace")[:800])
        on = st.toggle(f"📚 {sid}", value=(sid not in disabled),
                       key=f"sk_{aid}_{sid}", disabled=not _redis_ok,
                       help=(fm.get("description") or "")[:160])
        if _redis_ok:
            if on and sid in disabled:
                _redis.srem(rkey, sid)
            elif not on and sid not in disabled:
                _redis.sadd(rkey, sid)


def _parse_session(path):
    text = path.read_text(errors="replace")
    fm = _parse_fm(text[:500]) or {}
    aid = fm.get("agent_id")
    msgs = []
    if "## Messages" in text:
        body = text.split("## Messages", 1)[1].split("## _session_resume")[0]
        for block in body.split("\n### ")[1:]:
            head, _, content = block.partition("\n")
            role = "user" if head.strip().lower().startswith("user") else "assistant"
            content = content.strip()
            if content:
                msgs.append({"role": role, "type": "text", "content": content})
    msgs.reverse()
    return aid, msgs


def page_sessions():
    d = P["sessions"]
    files = sorted(d.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True) if d.exists() else []
    st.caption(f"{len(files)} sessions")
    if not files:
        st.caption("Aucune session dans 04_SESSIONS/.")
        return
    sel = st.selectbox("Session", [f.name for f in files], key="sess_sel")
    f = d / sel
    fm = _parse_fm(f.read_text(errors="replace")[:500]) or {}
    ts = time.strftime("%d/%m %Y %H:%M", time.localtime(f.stat().st_mtime))
    st.markdown(f"<div class='la-card'><b>{sel}</b><br><small>agent={fm.get('agent_id','?')} · "
                f"{fm.get('message_count','?')} msg · {fm.get('status','')} · {ts}</small></div>",
                unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    if c1.button("💬 Charger", key="sess_load", use_container_width=True):
        aid, msgs = _parse_session(f)
        if aid in st.session_state.histories:
            st.session_state.histories[aid] = msgs
            st.success(f"Chargé dans « {AGENT_MAP.get(aid, {}).get('label', aid)} » ({len(msgs)} msg).")
        else:
            st.error(f"agent_id « {aid} » inconnu.")
    if c2.button("📂 Ouvrir", key="sess_open", use_container_width=True):
        subprocess.Popen(["xdg-open", str(f)])
        st.info("Ouvert dans le lecteur.")
    if c3.button("🗑️ Supprimer", key="sess_del", use_container_width=True):
        subprocess.run(["gio", "trash", str(f)])
        st.warning(f"{sel} → corbeille.")
        st.rerun()


def page_memory():
    files = _list_md(P["memory"], 40)
    if not files:
        st.caption("Aucune note dans 05_MEMORY/.")
    for f in files:
        st.markdown(f"<div class='la-card'><b>{f.stem}</b></div>", unsafe_allow_html=True)
    if (P["memory"] / "memory_index.json").exists():
        st.caption("📇 Index mémoire présent (fichiers plats).")


def page_cron():
    files = _list_md(P["cron"], 40, recursive=True)
    if not files:
        st.caption("Aucun cron dans 07_CRON/.")
    for f in files:
        fm = _parse_fm(f.read_text(errors="replace")[:800])
        status = fm.get("status", "?")
        sched = fm.get("schedule", "")
        agent = fm.get("agent", fm.get("target", ""))
        st.markdown(f"<div class='la-card'><b>{f.stem}</b><br>"
                    f"<small>status={status} · {sched} · {agent}</small></div>",
                    unsafe_allow_html=True)


def page_logs():
    st.caption("🖥️ Terminal des agents — activité en direct (tous agents)")

    @st.fragment(run_every="1s")
    def _terminal():
        if _LIVE_LOG.exists():
            lines = _tail(_LIVE_LOG, 400).splitlines()
            txt = "\n".join(reversed(lines)) or "(en attente…)"
        else:
            txt = "(en attente d'activité des agents… lance start_LA.sh)"
        st.markdown(f"<div class='la-term'>{html.escape(txt)}</div>", unsafe_allow_html=True)

    _terminal()


def page_tools():
    if not P["tools"].exists():
        st.caption("agent/tools/ introuvable.")
        return
    tools = sorted(f.stem for f in P["tools"].glob("*.py")
                   if f.stem != "__init__" and not f.stem.startswith("_"))
    aid = st.selectbox("Agent", [a["id"] for a in AGENTS],
                       format_func=lambda x: AGENT_MAP[x]["label"], key="tools_agent")
    rkey = f"agent:{AGENT_MAP[aid]['key']}:tools_disabled"
    disabled = set(_redis.smembers(rkey)) if _redis_ok else set()
    st.caption(f"{len(tools)} outils · {len(disabled)} désactivé(s) · prise en compte au prochain message")
    if not _redis_ok:
        st.warning("Redis down : toggles indisponibles.")
    for t in tools:
        on = st.toggle(f"🔧 {t}", value=(t not in disabled),
                       key=f"tg_{aid}_{t}", disabled=not _redis_ok)
        if _redis_ok:
            if on and t in disabled:
                _redis.srem(rkey, t)
            elif not on and t not in disabled:
                _redis.sadd(rkey, t)


PAGES = {
    "Serveurs": page_servers,
    "Skills":   page_skills,
    "Sessions": page_sessions,
    "Mémoire":  page_memory,
    "Cron":     page_cron,
    "Logs":     page_logs,
    "Outils":   page_tools,
}