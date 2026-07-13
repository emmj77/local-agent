"""Routeur d outils - execute un tool_call et retourne le resultat valide."""

import importlib
import io
import json
import sys
import time
from datetime import datetime
from contextlib import redirect_stdout
from pathlib import Path
from pydantic import BaseModel, Field
from core.log_manager import log_local

_LIVE_LOG = Path(__file__).resolve().parent.parent / "log" / "agents_live.log"


def _write_live_log(agent_id: str, text: str):
    """Écrit une ligne dans agents_live.log (lu par le dashboard)."""
    try:
        _LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LIVE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{agent_id}] {text}\n")
    except Exception:
        pass


class RouterResult(BaseModel):
    success: bool = Field(..., description="Succes de l execution")
    output: str = Field(..., description="Resultat ou erreur de l outil")


def route_tool_call(tool_name: str, tool_args: dict, config=None, agent_id: str = "agent") -> dict:
    """Routeur robuste: capture stdout des outils, valide avec Pydantic, log automatique.

    Args:
        tool_name: Nom de l'outil (ex: "file_manage")
        tool_args: Arguments dict (ex: {"action": "read", "file_path": "/tmp/test.txt"})
        config: InstanceConfig (pour log_local — R18 chemins)
        agent_id: ID de l'agent (pour agents_live.log)
    """
    try:
        # C6 fix: valider tool_name + vérifier que le module existe avant import
        import re as _re
        import importlib.util as _ilu
        clean_name = tool_name.replace('.py', '').strip()
        if not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', clean_name):
            return {"success": False, "output": f"Nom d'outil invalide: {tool_name}"}

        module_path = f"tools.{clean_name}"
        # Vérifier que le module existe réellement (anti-injection)
        spec = _ilu.find_spec(module_path)
        if spec is None:
            return {"success": False, "output": f"Outil introuvable: {tool_name}"}
        module = importlib.import_module(module_path)

        if not hasattr(module, "run"):
            res = RouterResult(success=False, output=f"Outil {tool_name} invalide: pas de fonction run()")
            return res.model_dump()

        # Capturer stdout car les outils utilisent print() et non return
        args_json = json.dumps(tool_args, ensure_ascii=False)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            module.run(args_json)
        output_str = buffer.getvalue().strip()

        if not output_str:
            output_str = "[outil silencieux]"

        log_local(f"Tool: {tool_name} | Args: {tool_args}", output_str, config)
        _write_live_log(agent_id, f"$ {tool_name} {json.dumps(tool_args, ensure_ascii=False)[:120]}")
        res = RouterResult(success=True, output=output_str)

    except ModuleNotFoundError:
        error_msg = f"Outil introuvable: {tool_name}"
        log_local(f"Tool: {tool_name}", error_msg, config)
        res = RouterResult(success=False, output=error_msg)
    except ImportError as e:
        # Dépendance manquante dans l'outil ou module introuvable → erreur propre
        error_msg = f"Erreur import outil {tool_name}: {e}"
        log_local(f"Tool: {tool_name}", error_msg, config)
        res = RouterResult(success=False, output=error_msg)
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, ValueError, RuntimeError) as e:
        error_msg = f"Erreur execution {tool_name}: {e}"
        log_local(f"Tool: {tool_name} | Args: {tool_args}", error_msg, config)
        res = RouterResult(success=False, output=error_msg)

    return res.model_dump()
