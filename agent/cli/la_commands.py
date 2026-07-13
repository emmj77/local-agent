"""la_commands — Registre des commandes slash du CLI Local_Agent.

Inspiré de hermes_cli/commands.py (Hermes Agent).
Chaque commande = (nom, description, handler).
Le handler reçoit (args_str, ctx) où ctx = {client, agent_id, agent_info, ...}.
"""
import json
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent  # cli/ → agent/
ROOT = AGENT_DIR.parent  # Local_Agent/


# ── Handlers ──────────────────────────────────────────────

def cmd_agent(args: str, ctx: dict) -> str:
    """Change l'agent cible. Usage: /agent agent1"""
    from cli.la_client import AGENTS
    if not args.strip():
        cur = ctx["agent_id"]
        info = AGENTS.get(cur, {})
        return f"Agent actuel: {cur} ({info.get('label', '?')} · {info.get('model', '?')})"
    aid = args.strip()
    if aid not in AGENTS:
        return f"Agent inconnu: {aid}. Agents: {', '.join(AGENTS)}"
    ctx["agent_id"] = aid
    ctx["agent_key"] = AGENTS[aid]["key"]
    return f"Agent → {aid} ({AGENTS[aid]['label']} · {AGENTS[aid]['model']})"


def cmd_model(args: str, ctx: dict) -> str:
    """Override modèle. Usage: /model deepseek-chat"""
    if not args.strip():
        return "Usage: /model <nom_modèle>"
    model = args.strip()
    key = f"agent:{ctx['agent_key']}:model_override"
    ctx["client"].redis.set(key, model)
    return f"Modèle → {model} (override Redis: {key})"


def cmd_project(args: str, ctx: dict) -> str:
    """Liste/Ajoute projets. Usage: /project [add <id> <titre>]"""
    projects_dir = ROOT / "02_PROJETS"
    args = args.strip()

    if args.startswith("add "):
        # /project add mon_projet "Mon Titre"
        parts = args[4:].strip().split(" ", 1)
        if len(parts) < 2:
            return "Usage: /project add <id> <titre>"
        pid, title = parts[0], parts[1].strip('"').strip("'")
        try:
            from tools.project_manage import run
            import io, sys as _sys
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                run(json.dumps({"action": "add", "project_id": pid, "title": title, "summary": "",
                                "tags": [], "status": "active"}))
            return buf.getvalue().strip()
        except Exception as e:
            return f"Erreur: {e}"

    # Liste
    if not projects_dir.exists():
        return "Aucun projet (02_PROJETS/ vide)."
    files = sorted(projects_dir.glob("PROJECT_*.md"))
    if not files:
        return "Aucun projet."
    lines = []
    for f in files:
        content = f.read_text(errors="replace")[:300]
        fm = _parse_fm(content)
        status = fm.get("status", "?")
        title = fm.get("title", f.stem)
        lines.append(f"  {f.stem}  [{status}]  {title}")
    return "Projets:\n" + "\n".join(lines) + f"\n  ({len(lines)} projet(s))"


def cmd_sessions(args: str, ctx: dict) -> str:
    """Liste/Charge sessions. Usage: /sessions [load <id>]"""
    d = ROOT / "04_SESSIONS"
    args = args.strip()

    if args.startswith("load "):
        sid = args[5:].strip()
        files = list(d.glob(f"*{sid}*.md")) if d.exists() else []
        if not files:
            return f"Session '{sid}' introuvable."
        f = files[0]
        try:
            aid, msgs = _parse_session(f)
            if aid and "histories" in ctx:
                ctx["histories"][aid] = msgs
                return f"Session chargée: {f.name} → agent {aid} ({len(msgs)} msg)."
            return f"Session {f.name} chargée mais agent_id inconnu."
        except Exception as e:
            return f"Erreur chargement: {e}"

    # Liste
    if not d.exists():
        return "Aucune session (04_SESSIONS/ vide)."
    files = sorted(d.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:15]
    lines = []
    for f in files:
        ts = time.strftime("%d/%m %H:%M", time.localtime(f.stat().st_mtime))
        fm = _parse_fm(f.read_text(errors="replace")[:300])
        agent = fm.get("agent_id", "?")
        msgs = fm.get("message_count", "?")
        status = fm.get("status", "")
        lines.append(f"  {f.stem}  [{agent} · {msgs} msg · {status}]  {ts}")
    return "Sessions:\n" + "\n".join(lines) + f"\n  ({len(lines)} session(s), /sessions load <id> pour charger)"


def cmd_tools(args: str, ctx: dict) -> str:
    """Liste les outils disponibles. Usage: /tools"""
    tools_dir = AGENT_DIR / "tools"
    if not tools_dir.exists():
        return "agent/tools/ introuvable."
    tools = sorted(f.stem for f in tools_dir.glob("*.py")
                   if f.stem != "__init__" and not f.name.startswith("_"))
    return f"Outils ({len(tools)}):\n  " + "\n  ".join(tools)


def cmd_skills(args: str, ctx: dict) -> str:
    """Liste les skills. Usage: /skills"""
    d = ROOT / "03_SKILLS"
    if not d.exists():
        return "Aucun skill (03_SKILLS/ vide)."
    smds = sorted(d.glob("*/SKILL.md"))
    lines = []
    for smd in smds:
        fm = _parse_fm(smd.read_text(errors="replace")[:500])
        name = fm.get("name", smd.parent.name)
        status = fm.get("status", "?")
        lines.append(f"  {smd.parent.name}  [{status}]  {name}")
    return f"Skills ({len(lines)}):\n" + "\n".join(lines)


def cmd_memory(args: str, ctx: dict) -> str:
    """Gère la mémoire. Usage: /memory [list|search <query>|add <texte>]"""
    args = args.strip()
    mem_dir = ROOT / "05_MEMORY"

    if args.startswith("search "):
        query = args[7:].strip()
        try:
            from core.router import route_tool_call
            res = route_tool_call("memory_manage", {"action": "search", "query": query, "limit": 5})
            if res.get("success"):
                data = json.loads(res["output"])
                results = data.get("results", [])
                lines = [f"  [{r.get('similarity', 0):.2f}] {r.get('content', '')[:120]}"
                         for r in results]
                return f"Mémoire (recherche: {query}):\n" + "\n".join(lines)
            return f"Erreur: {res.get('output', '?')}"
        except Exception as e:
            return f"Erreur: {e}"

    if args.startswith("add "):
        return "Usage: envoie un message normal, l'agent utilisera memory_manage pour ajouter."

    # Liste (défaut)
    if not mem_dir.exists():
        return "Aucune mémoire (05_MEMORY/ vide)."
    files = sorted(
        (f for f in mem_dir.iterdir() if f.is_file() and f.name.endswith(".md")),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )[:15]
    lines = [f"  {f.stem}" for f in files]
    return f"Mémoire ({len(files)}):\n" + "\n".join(lines) + \
           f"\n  /memory search <query> pour recherche vectorielle"


def cmd_stop(args: str, ctx: dict) -> str:
    """Arrête l'agent en cours. Usage: /stop"""
    ctx["client"].send_stop(ctx["agent_key"])
    return "🛑 Signal /stop envoyé."


def cmd_new(args: str, ctx: dict) -> str:
    """Nouvelle session (clear historique local). Usage: /new"""
    aid = ctx["agent_id"]
    if "histories" in ctx:
        ctx["histories"][aid] = []
    return "🆕 Nouvelle session — historique vidé."


def cmd_help(args: str, ctx: dict) -> str:
    """Aide. Usage: /help [commande]"""
    if args.strip():
        name = args.strip().lstrip("/")
        cmd = REGISTRY.get(name)
        if cmd:
            return f"/{name} — {cmd[1]}"
        return f"Commande inconnue: {name}"
    lines = [f"  /{name:<14} {desc}" for name, (desc, _) in REGISTRY.items()]
    return "Commandes:\n" + "\n".join(lines)


def cmd_quit(args: str, ctx: dict) -> str:
    """Quitte le CLI. Usage: /quit"""
    ctx["quit"] = True
    return "👋 Au revoir."


# ── Registre ──────────────────────────────────────────────

REGISTRY: dict[str, tuple[str, callable]] = {
    "agent":    ("Changer d'agent — /agent <agent1|agent2|agentlocal|...>", cmd_agent),
    "model":    ("Override modèle — /model <nom>", cmd_model),
    "project":  ("Projets — /project [add <id> <titre>]", cmd_project),
    "sessions": ("Sessions — /sessions [load <id>]", cmd_sessions),
    "tools":    ("Outils disponibles — /tools", cmd_tools),
    "skills":   ("Skills — /skills", cmd_skills),
    "memory":   ("Mémoire — /memory [list|search <q>]", cmd_memory),
    "stop":     ("Arrêter l'agent — /stop", cmd_stop),
    "new":      ("Nouvelle session — /new", cmd_new),
    "help":     ("Aide — /help [commande]", cmd_help),
    "quit":     ("Quitter — /quit", cmd_quit),
    "q":        ("Quitter (raccourci) — /q", cmd_quit),
}


def dispatch(line: str, ctx: dict) -> str | None:
    """Parse et exécute une commande slash. Retourne la réponse ou None si pas une commande."""
    if not line.startswith("/"):
        return None
    parts = line[1:].split(maxsplit=1)
    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    cmd = REGISTRY.get(name)
    if cmd is None:
        return f"Commande inconnue: /{name}. Tape /help."
    return cmd[1](args, ctx)


# ── Helpers ────────────────────────────────────────────────

def _parse_fm(text: str) -> dict:
    """Parse YAML frontmatter naïf."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def _parse_session(path: Path):
    """Parse un .md de session → (agent_id, [messages])."""
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
