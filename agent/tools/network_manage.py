"""Outil network_manage — fusion de http_get_request et download_file.

Actions:
  - get     : GET HTTP(S) avec garde SSRF + timeout + plafond taille texte.
  - download: télécharge une URL vers un fichier (garde SSRF + timeout + plafond 100 Mo,
              destination filtrée par guarded_path).

Interface: run(json_args: str) — accepte un JSON avec 'action' + paramètres.
"""

import sys
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/
import requests
from security.net_guard import (
    safe_get,
    read_capped,
    UrlBlockedError,
    DEFAULT_TIMEOUT,
    MAX_TEXT_BYTES,
    MAX_DOWNLOAD_BYTES,
)
from security.safe_path import guarded_path, PathBlockedError


class NetworkManageArgs(BaseModel):
    action: Literal["get", "download"]
    url: str
    dest: str | None = Field(None, description="Chemin de destination (action='download' uniquement)")
    timeout: int = Field(DEFAULT_TIMEOUT, description="Timeout en secondes")


def run(json_args: str):
    try:
        args = NetworkManageArgs.model_validate_json(json_args)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    if args.action == "get":
        _do_get(args)
    elif args.action == "download":
        _do_download(args)


def _do_get(args: NetworkManageArgs):
    resp = None
    try:
        resp = safe_get(args.url, timeout=args.timeout)
        data = read_capped(resp, MAX_TEXT_BYTES)
        text = data.decode(resp.encoding or "utf-8", errors="replace")
        print(json.dumps({"status": "success", "http_status": resp.status_code, "data": text},
                         ensure_ascii=False))
    except UrlBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (requests.RequestException, OSError, ValueError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))
    finally:
        if resp is not None:
            resp.close()


def _do_download(args: NetworkManageArgs):
    if not args.dest:
        print(json.dumps({"status": "error", "message": "Le paramètre 'dest' est requis pour l'action 'download'"}))
        return

    try:
        dest = guarded_path(args.dest)
    except PathBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        return

    resp = None
    try:
        resp = safe_get(args.url, timeout=args.timeout)
        cl = resp.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_DOWNLOAD_BYTES:
            print(json.dumps({"status": "error",
                              "message": f"Fichier trop volumineux: {cl} > {MAX_DOWNLOAD_BYTES} octets"}))
            return
        total = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(65536):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    f.close()
                    dest.unlink(missing_ok=True)
                    print(json.dumps({"status": "error",
                                      "message": f"Téléchargement dépasse {MAX_DOWNLOAD_BYTES} octets — annulé"}))
                    return
                f.write(chunk)
        print(json.dumps({"status": "success", "dest": str(dest), "bytes": total}))
    except UrlBlockedError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
    except (requests.RequestException, OSError, ValueError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))
    finally:
        if resp is not None:
            resp.close()


if __name__ == "__main__":
    run(sys.argv[1])