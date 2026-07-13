---
name: obscura
endpoint: http://127.0.0.1:3000 (mode `obscura mcp --http`)
status: installed
description: "Obscura — navigateur headless (V8/CDP) pour agents & scraping. fetch/scrape/serve + serveur MCP natif."
binary: /home/moussa/Applications/obscura/obscura
tools:
  - fetch
  - scrape
  - serve
  - mcp
---

## Obscura MCP

Navigateur headless léger (remplaçant de Chrome headless, Puppeteer/Playwright-compatible via CDP).

### Installé
- Binaire : `/home/moussa/Applications/obscura/obscura` (+ `obscura-worker`), symlink `~/.local/bin/obscura`.
- Testé : `obscura fetch https://example.com --eval "document.title"` → OK.

### Modes
- `obscura fetch <url> --eval "<js>"` — charge + exécute du JS.
- `obscura scrape <url>` — extraction de contenu.
- `obscura serve` — API HTTP (CDP, port 9222).
- `obscura mcp --http --host 127.0.0.1 --port 3000` — **serveur MCP** (pour l'agent).

### Options utiles
- `--stealth` (fingerprint constant), `--proxy`, `--user-agent`, `--allow-private-network`.

### Intégration (à faire)
- Lancer `obscura mcp --http --port 3000` et brancher le client MCP de l'agent dessus.
