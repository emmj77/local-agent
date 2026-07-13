#!/bin/bash
# Lanceur CLI Local Agent — standalone, zéro dépendance externe.
# Usage: la [-q "msg"] [-a agent] [-m model]
# Installation: ln -s $(pwd)/agent/cli/la.sh ~/.local/bin/la

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
AGENT_DIR="$(dirname "$SCRIPT_DIR")"
ROOT="$(dirname "$AGENT_DIR")"
VENV="$ROOT/.venv"

[ -f "$VENV/bin/activate" ] && source "$VENV/bin/activate"
cd "$AGENT_DIR"
exec python3 cli/la_tui.py "$@"
