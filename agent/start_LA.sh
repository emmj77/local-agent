#!/bin/bash
# start_LA.sh — LANCEUR UNIQUE de Local_Agent (appelé par le .desktop, Terminal=false).
# Redis + 6 agents + dashboard Streamlit + fenêtre Chromium (--app), tout en un.
# Logs dans agent/log/

cd /home/moussa/Applications/Local_Agent

VENV=".venv/bin"
LOGDIR="agent/log"
PIDFILE="$LOGDIR/la_pids"
URL="http://127.0.0.1:8000"
PROFILE="$HOME/.local/share/local-agent-chromium"

mkdir -p "$LOGDIR" "$PROFILE"
: > "$PIDFILE"

# 1. Tuer TOUS les anciens processus de la stack (SIGTERM, puis SIGKILL si récalcitrant).
#    Inclut log_aggregator + router_daemon (oubliés avant -> doublons empilés).
_kill_all() {
  for pat in "agent_server.py" "log_aggregator.py" "router_daemon.py" "streamlit run agent/dashboard"; do
    pkill -f "$pat" 2>/dev/null
  done
  sleep 2
  # Achever les récalcitrants (daemons bloqués réseau/asyncio)
  for pat in "agent_server.py" "log_aggregator.py" "router_daemon.py" "streamlit run agent/dashboard"; do
    pkill -9 -f "$pat" 2>/dev/null
  done
  pkill -f "redis-server.*6379" 2>/dev/null
  sleep 1
}
_kill_all

# 2. Redis (binaire SYSTÈME /usr/bin, PAS le venv) + attente PONG
redis-server --port 6379 --daemonize yes --logfile "$LOGDIR/redis.log" 2>/dev/null
for i in $(seq 1 10); do
  redis-cli ping 2>/dev/null | grep -q PONG && break
  sleep 1
done

# 2b. Agrégateur de logs (terminal unifié live de tous les agents)
setsid "$VENV/python3" agent/log_aggregator.py \
  > "$LOGDIR/log_aggregator.log" 2>&1 < /dev/null &
echo $! >> "$PIDFILE"

# 3. Les 6 agents
for cfg in agent1 agent2 agent3 agentlocal subagent1 subagent2; do
  setsid "$VENV/python3" agent/agent_server.py --config "agent/configs/${cfg}.yaml" \
    > "$LOGDIR/${cfg}.log" 2>&1 < /dev/null &
  echo $! >> "$PIDFILE"
done

# 3b. Router Telegram (pont Telegram <-> agents via Redis). Nécessite un TELEGRAM_BOT_TOKEN valide.
setsid "$VENV/python3" agent/telegram_router/router_daemon.py \
  > "$LOGDIR/telegram.log" 2>&1 < /dev/null &
echo $! >> "$PIDFILE"

# 4. Dashboard Streamlit
if ! curl -s -o /dev/null "$URL"; then
  setsid "$VENV/streamlit" run agent/dashboard/dashboard.py \
    --server.port 8000 --server.address 127.0.0.1 --server.headless true \
    > "$LOGDIR/dashboard.log" 2>&1 < /dev/null &
  echo $! >> "$PIDFILE"
  for i in $(seq 1 50); do curl -s -o /dev/null "$URL" && break; sleep 0.5; done
fi

# 5. Fenêtre Chromium en mode application (profil isolé). Fallback navigateur défaut.
rm -f "$PROFILE"/Singleton* 2>/dev/null
chromium \
  --app="$URL" \
  --user-data-dir="$PROFILE" \
  --class=local-agent-dashboard \
  --name=local-agent-dashboard \
  --window-size=1500,950 \
  --no-first-run --no-default-browser-check --disable-features=Translate \
  > "$LOGDIR/chromium_app.log" 2>&1 &
CHROME_PID=$!
sleep 3
if ! kill -0 "$CHROME_PID" 2>/dev/null; then
  echo "[start_LA] Chromium n'a pas démarré — fallback navigateur par défaut." >> "$LOGDIR/chromium_app.log"
  xdg-open "$URL" >/dev/null 2>&1 &
  CHROME_PID=""
fi

# 6. Teardown : quand la fenêtre se ferme (ou signal), on tue TOUTE la stack.
#    -> fermer Local Agent = tous les agents meurent (plus d'orphelins).
_teardown() {
  echo "[start_LA] fermeture — arrêt de la stack…"
  _kill_all
  exit 0
}
trap _teardown SIGINT SIGTERM

if [ -n "$CHROME_PID" ]; then
  wait "$CHROME_PID"   # bloque tant que la fenêtre est ouverte
  _teardown            # fenêtre fermée -> teardown
fi
