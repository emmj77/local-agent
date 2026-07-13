"""system_manage — outil fusionné: status (CPU/RAM) et tokens (estimation).

Actions:
  - status : lecture seule, état CPU/RAM via psutil.
  - tokens : estimation du nombre de tokens dans un texte (approximation français/anglais).
"""
import sys
import json
from typing import Literal, Optional

import psutil
from pydantic import BaseModel, model_validator


class SystemManageArgs(BaseModel):
    action: Literal["status", "tokens"]
    # --- champs pour action="tokens" ---
    text: Optional[str] = None

    @model_validator(mode="after")
    def _check_text_required(self):
        """``text`` est obligatoire quand action == 'tokens'."""
        if self.action == "tokens" and not self.text:
            raise ValueError("Le champ 'text' est obligatoire pour action='tokens'.")
        return self


def run(json_args: str = "{}"):
    try:
        args = SystemManageArgs.model_validate_json(json_args)

        if args.action == "status":
            metrics = {
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
            }
            print(json.dumps({"status": "success", "metrics": metrics}))

        elif args.action == "tokens":
            # Approximation: ~3.5 caractères/token pour le français, ~4 pour l'anglais.
            # On utilise 3.5 (l'agent parle français) avec un minimum de 1.
            text = args.text or ""
            count = max(1, round(len(text) / 3.5))
            print(json.dumps({"status": "success", "token_count": count}))

    except (ValueError, KeyError, RuntimeError, FileNotFoundError, PermissionError,
            ImportError, json.JSONDecodeError, TypeError, OSError) as e:
        print(json.dumps({"status": "error", "message": str(e)}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")