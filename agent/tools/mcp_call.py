"""Outil mcp_call — appelle un serveur MCP (HTTP JSON-RPC) et renvoie le résultat.

Donne aux agents l'accès aux serveurs MCP actifs (démarrés via le dashboard).

Args JSON:
  {"server": "obscura"}                                  -> liste les outils (tools/list)
  {"server": "obscura", "tool": "browser_navigate",
   "arguments": {"url": "https://example.com"}}          -> appelle l'outil (tools/call)

R6 (run(json_args)), R9 (un fichier = une fonction métier).
"""

import sys
import json
import urllib.request
from pydantic import BaseModel, Field

# Serveur MCP -> URL HTTP JSON-RPC. Étendre quand d'autres serveurs exposent --http.
_SERVERS = {
    "obscura": "http://127.0.0.1:3000/mcp",
}


class McpCallArgs(BaseModel):
    server: str = Field(..., description="Nom du serveur MCP (ex: obscura)")
    tool: str | None = Field(None, description="Outil MCP; vide = liste les outils")
    arguments: dict = Field(default_factory=dict, description="Arguments de l'outil MCP")


def _rpc(url: str, method: str, params: dict | None = None) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def run(json_args: str):
    try:
        a = McpCallArgs.model_validate_json(json_args)
    except (ValueError, TypeError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    url = _SERVERS.get(a.server)
    if not url:
        print(json.dumps({"status": "error",
                          "message": f"Serveur MCP inconnu: {a.server}. Connus: {list(_SERVERS)}. "
                                     "Lancer obscura: `obscura mcp --http --port 3000`."}))
        return

    try:
        if not a.tool:
            res = _rpc(url, "tools/list")
            tools = [{"name": t.get("name"), "description": t.get("description", "")}
                     for t in res.get("result", {}).get("tools", [])]
            print(json.dumps({"status": "success", "server": a.server, "tools": tools},
                             ensure_ascii=False))
            return
        res = _rpc(url, "tools/call", {"name": a.tool, "arguments": a.arguments})
        if "error" in res:
            print(json.dumps({"status": "error", "message": res["error"]}, ensure_ascii=False))
            return
        content = res.get("result", {}).get("content", [])
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        print(json.dumps({"status": "success", "server": a.server, "tool": a.tool,
                          "result": text or res.get("result")}, ensure_ascii=False))
    except (urllib.error.URLError, OSError) as e:
        print(json.dumps({"status": "error",
                          "message": f"Serveur MCP '{a.server}' injoignable ({e}). "
                                     "Lancer: `obscura mcp --http --port 3000`."}))
    except (ValueError, TypeError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
