"""Chargeur de contexte — lit SOUL.md + context.json.

R9: Un fichier = une fonction métier (ici: chargement du SOUL).
R18: Chemins centralisés via config (InstanceConfig).
R21: Validation stricte via Pydantic (ContextModel).
"""


import json
from pathlib import Path
from pydantic import BaseModel, Field


class MessageItem(BaseModel):
    """Un élément de l'historique de conversation."""
    role: str
    content: str


class ContextModel(BaseModel):
    """Structure stricte attendue dans context.json (R21)."""
    session_id: str = ""
    session_resume: str = ""
    messages: list[MessageItem] = Field(default_factory=list)


def load_context_files(agent_dir: str, config) -> dict:
    """Charge SOUL.md + context.json.

    Retourne un dict contenant:
      - "soul": le SOUL complet (SOUL.md + resume session)
      - "history": la liste des messages validés de l'historique
    """
    vault_root = Path(config.paths.vault_root)
    context_json_path = Path(config.paths.context_json)

    # Chargement ou initialisation sécurisée de context.json
    raw_context = {}
    if context_json_path.exists():
        try:
            raw_context = json.loads(context_json_path.read_text(encoding="utf-8"))
        except Exception:
            raw_context = {}

    # Validation Pydantic forcée (R21)
    try:
        validated_context = ContextModel(**raw_context)
    except Exception:
        validated_context = ContextModel()

    # Lecture du SOUL (unique source de règles)
    from security.safe_path import safe_path
    soul = ""
    soul_path = vault_root / "SOUL.md"
    if soul_path.exists():
        try:
            soul = safe_path(str(soul_path)).read_text(encoding="utf-8")
        except Exception:
            pass

    # Ajout du resume de session si présent
    if validated_context.session_resume:
        soul = f"{soul}\n\n== RESUME HISTORIQUE ==\n{validated_context.session_resume}"

    # Conversion du format MessageItem vers le format brut attendu par le payload
    history_list = [
        {"role": m.role, "content": m.content}
        for m in validated_context.messages
    ]

    return {
        "soul": soul,
        "history": history_list,
    }
