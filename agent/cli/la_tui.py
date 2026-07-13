#!/usr/bin/env python3
"""la — Terminal TUI Local Agent. Layout Hermes : bannière + chat scrollable + input en bas.
Redis si dispo, sinon AgentLoop standalone. Toujours fonctionnel.
"""
import os, sys, threading, time, shutil, argparse, readline, json
from datetime import datetime
from pathlib import Path

# ── LA backend ──
_AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENT_DIR))
os.chdir(str(_AGENT_DIR))

_HIST_FILE = Path.home() / ".local" / "share" / "la" / "history.txt"
VERSION = "1.0.0"
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_LIVE_LOG = _AGENT_DIR / "log" / "agents_live.log"

_ENDPOINT_LABEL = {
    "cloud": "Ollama Cloud", "cloud_free": "Cloud Free", "cloud_paid": "Cloud Paid",
    "deepseek_api": "DeepSeek", "llamacpp": "llama.cpp", "openrouter": "OpenRouter",
    "gemini": "Gemini", "nvidia": "NVIDIA", "mistral": "Mistral", "xai": "xAI", "groq": "Groq",
}
_AGENTS = {
    "agent1":    {"key": "agent1",    "model": "glm-5.2",              "endpoint": "cloud"},
    "agent2":    {"key": "agent2",    "model": "gemma4:31b",          "endpoint": "cloud"},
    "agent3":    {"key": "agent3",    "model": "deepseek-reasoner",   "endpoint": "deepseek_api"},
    "agentlocal":{"key": "agentlocal","model": "llama-cpp-local",     "endpoint": "llamacpp"},
    "subagent1": {"key": "subagent1", "model": "gemini-2.5-flash-lite","endpoint": "gemini"},
    "subagent2": {"key": "subagent2", "model": "TBD",                 "endpoint": "cloud"},
}

_RICH = False
try:
    from rich.console import Console; _RICH = True
except ImportError: pass


def _redis_ok():
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
        r.ping(); r.close(); return True
    except: return False


def _send_redis(key, prompt, timeout_ms=120000):
    from cli.redis_client import send_to_agent, subscribe_events
    evs = []
    def _l():
        try:
            for e in subscribe_events(key):
                if e.get("type") == "tool_start": evs.append(e)
        except: pass
    t = threading.Thread(target=_l, daemon=True); t.start()
    return send_to_agent(key, prompt, timeout_ms=timeout_ms), evs


def _send_standalone(aid, model, prompt):
    from config import InstanceConfig, AgentConfig, EnvSettings
    from agent_loop import AgentLoop
    cfg = AgentConfig(agent_id=aid, redis_key=aid, model=model, endpoint="cloud",
                      name=aid, context_window=128768, max_tool_calls=300, timeout_http=120)
    c = InstanceConfig(agent=cfg, env=EnvSettings()); c.paths = c.paths.resolve()
    loop = AgentLoop(c)
    result = [None]; done = threading.Event(); tag = f"[{aid}]"; evs = []
    def _r():
        try: result[0] = loop.run(prompt)
        except Exception as e: result[0] = {"text": f"Erreur: {e}"}
        done.set()
    t = threading.Thread(target=_r, daemon=True); t.start()
    off = _LIVE_LOG.stat().st_size if _LIVE_LOG.exists() else 0
    seen = set(); si = 0
    while not done.is_set():
        si = (si + 1) % len(_SPINNER)
        if _LIVE_LOG.exists():
            try:
                with open(_LIVE_LOG, "r", errors="replace") as f:
                    f.seek(off); new = f.read(); off = f.tell()
            except OSError: new = ""
            for ln in new.splitlines():
                if tag in ln and ln.split(tag, 1)[1].strip().startswith("$ "):
                    rest = ln.split(tag, 1)[1].strip()[2:].strip()
                    parts = rest.split(" ", 1); key = parts[0]
                    if key not in seen:
                        seen.add(key)
                        args_str = parts[1][:120] if len(parts) > 1 else ""
                        try:
                            d = json.loads(args_str)
                            args_str = " ".join(f"{k}:{str(v)[:40]}" for k, v in d.items())[:100]
                        except: pass
                        evs.append({"tool": key, "args": args_str})
        time.sleep(0.15)
    t.join(timeout=5)
    return result[0] or {"text": "⏰"}, evs


# ── TUI (prompt_toolkit) ──
def _banner(aid, info, mode, w):
    provider = _ENDPOINT_LABEL.get(info.get("endpoint", ""), "?")
    model = info.get("model", "?")
    bar = f"╭{'─' * (w - 2)}╮"
    name = f"│  🤖 {aid} · {provider} · {model} · {mode}".ljust(w - 1) + "│"
    help_line = f"│  /help  |  Ctrl+C stop  |  Ctrl+D quit  |  Alt+Enter ↵".ljust(w - 1) + "│"
    return f"{bar}\n{name}\n{help_line}\n╰{'─' * (w - 2)}╯"


def _tui(aid, model, query):
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.history import FileHistory

    mode = "redis" if _redis_ok() else "standalone"
    _aid = aid
    _model = model

    # Curseur poussé en bas (pattern Hermes)
    try:
        tl = shutil.get_terminal_size().lines
        if tl > 2: print("\n" * (tl - 1), end="", flush=True)
    except: pass

    try: w = shutil.get_terminal_size().columns
    except: w = 80
    w = max(w, 60)

    print(_banner(aid, _AGENTS[aid], mode, w))
    busy = False; stop = threading.Event()

    def _cout(*a, **kw):
        with patch_stdout(): print(*a, **kw)

    # ── Input TextArea (pattern Hermes) ──
    _HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    input_area = TextArea(
        height=Dimension(min=1, max=8, preferred=1),
        width=Dimension(preferred=w - 4),
        prompt="> ",
        style="class:input",
        multiline=True,
        wrap_lines=True,
        history=FileHistory(str(_HIST_FILE)),
    )

    def _send(buf=None):
        nonlocal busy
        text = input_area.text.rstrip("\n")
        if not text.strip(): input_area.text = ""; return
        input_area.text = ""
        if text.startswith("/"):
            cmd = text[1:].strip().lower()
            if cmd in ("q", "quit"): _cout("  👋"); os._exit(0)
            elif cmd == "help": _cout("  /help  /clear  /quit  /agent <id>  /model <n>")
            elif cmd == "clear": os.system("clear"); print(_banner(aid, _AGENTS[aid], mode, w))
            elif cmd.startswith("agent "):
                new = cmd.split()[1]
                nonlocal _aid, _model
                if new in _AGENTS:
                    _aid = new; _model = _AGENTS[new]["model"]
                    _cout(f"  ✓ {_aid} · {_model}")
                else: _cout(f"  ✗ {new}")
            elif cmd.startswith("model "):
                _model = cmd.split(" ", 1)[1]; _cout(f"  ✓ {_model}")
            else: _cout(f"  ? {text}")
            return
        if busy: _cout("  ⏳ Occupé…"); return
        busy = True; stop.clear()

        # Affichage question
        _cout(f"\n  ╭─ Vous")
        for l in text.splitlines(): _cout(f"  │ {l}")
        _cout(f"  ╰──")

        # Lancement
        def _work():
            nonlocal busy, mode
            if mode == "redis":
                try:
                    res, evs = _send_redis(_AGENTS[aid]["key"], text)
                except:
                    mode = "standalone"
                    res, evs = _send_standalone(aid, model, text)
            else:
                res, evs = _send_standalone(aid, model, text)

            for ev in evs:
                _cout(f"  🔧 {ev.get('tool','?')}  {ev.get('args','')}")
            _cout(f"\n  {res.get('text', '(vide)')}\n")
            busy = False

        threading.Thread(target=_work, daemon=True).start()

    input_area.accept_handler = _send

    # ── Status bar ──
    def _status():
        dot = "🟡" if busy else "🟢"
        provider = _ENDPOINT_LABEL.get(_AGENTS[aid].get("endpoint", ""), "mode")
        return f" {dot} {aid} · {provider} · {_model} · {mode}  │  Ctrl+D quit  │  Ctrl+C stop  │  Alt+Enter ↵"

    status_bar = Window(content=FormattedTextControl(text=_status), height=1, style="class:status")

    root = HSplit([
        Window(height=1, char="─", style="class:sep"),
        input_area,
        Window(height=1, char="─", style="class:sep"),
        status_bar,
    ])

    style = Style.from_dict({
        "sep": "#30363d",
        "status": "bg:#161b22 #8b949e",
        "input": "bg:#0d1117 #c9d1d9",
    })

    kb = KeyBindings()

    @kb.add("c-d")
    def _(e):
        if not input_area.text: e.app.exit()

    @kb.add("c-c")
    def _(e):
        nonlocal busy
        if busy: stop.set(); busy = False
        else: e.app.exit()

    @kb.add("escape", "enter")
    def _(e):
        # Alt+Enter = submit (comportement par défaut multiline) → accept_handler
        input_area.buffer.validate_and_handle()

    # One-shot (sans TUI, rendu simple)
    if query:
        print(f"\n  ╭─ Q")
        print(f"  │ {query[:80]}")
        print(f"  ╰──")
        if mode == "redis":
            try: res, evs = _send_redis(_AGENTS[aid]["key"], query)
            except: mode = "standalone"; res, evs = _send_standalone(aid, model, query)
        else:
            res, evs = _send_standalone(aid, model, query)
        for ev in evs: print(f"  🔧 {ev.get('tool','?')}  {ev.get('args','')}")
        print(f"\n  {res.get('text', '(vide)')}\n")
        return

    app = Application(
        layout=Layout(root, focused_element=input_area),
        key_bindings=kb, style=style,
        full_screen=False, mouse_support=False, erase_when_done=True,
    )
    app.run()


def main():
    p = argparse.ArgumentParser(prog="la", description="Local Agent — terminal IA")
    p.add_argument("-q", "--query", type=str, default=None)
    p.add_argument("-a", "--agent", type=str, default="agent1", choices=list(_AGENTS))
    p.add_argument("-m", "--model", type=str, default=None)
    args = p.parse_args()
    model = args.model or _AGENTS[args.agent]["model"]
    _tui(args.agent, model, args.query)


if __name__ == "__main__":
    main()
