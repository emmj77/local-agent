"""tool_registry — génère les schémas JSON OpenAI pour le tool calling natif.

Scan tools/*.py au démarrage, lit les classes BaseModel Pydantic,
convertit en JSON Schema OpenAI-compatible.

Pour les outils sans BaseModel (dispatch par action, args manuels),
des modèles de fallback sont définis ici.

R6: Interface d'outil (def run(json_args: str)).
R9: Un fichier = une fonction métier.
"""
import ast
import importlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_log = logging.getLogger("tool_registry")

_AGENT_DIR = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _AGENT_DIR / "tools"


# --- Outils de contrôle (think + final_answer) — injectés à chaque tour ---

_CONTROL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "À utiliser pour planifier, analyser des données, ou expliquer "
                "la stratégie avant d'agir. Remplace le texte brut de réflexion."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought_process": {
                        "type": "string",
                        "description": "La réflexion de l'agent: analyse, plan, stratégie.",
                    },
                },
                "required": ["thought_process"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": (
                "À appeler UNIQUEMENT lorsque la tâche est accomplie pour "
                "délivrer le résultat final à l'utilisateur."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "string",
                        "description": "Le message final à délivrer à l'utilisateur.",
                    },
                },
                "required": ["response"],
            },
        },
    },
]

# --- Modèles Pydantic de fallback pour outils sans BaseModel ---
# v22: La plupart des outils ont leur propre BaseModel. Fallback vide.

_FALLBACK_MODELS: dict[str, type[BaseModel]] = {}


# --- Registry ---

def _extract_basemodel_class(source: str) -> str | None:
    """Extrait le nom de la première classe BaseModel du source."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id == "BaseModel":
                    return node.name
    return None


def _extract_docstring(source: str) -> str:
    """Extrait la docstring du module."""
    tree = ast.parse(source)
    if tree.body and isinstance(tree.body[0], ast.Expr):
        val = tree.body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            return val.value.strip().split("\n")[0]
    return ""


def _build_tool_schema(tool_name: str) -> dict[str, Any] | None:
    """Construit le schéma OpenAI pour un outil."""
    tool_path = _TOOLS_DIR / f"{tool_name}.py"
    if not tool_path.exists():
        return None

    source = tool_path.read_text(encoding="utf-8")
    description = _extract_docstring(source)

    # 1. Chercher une classe BaseModel dans le fichier
    model_name = _extract_basemodel_class(source)

    model_class = None
    if model_name:
        # Importer dynamiquement le module pour accéder à la classe
        try:
            spec = importlib.util.spec_from_file_location(
                f"tools.{tool_name}", str(tool_path)
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                # Ajouter agent/ au path pour les imports internes
                if str(_AGENT_DIR) not in sys.path:
                    sys.path.insert(0, str(_AGENT_DIR))
                spec.loader.exec_module(module)
                model_class = getattr(module, model_name, None)
        except Exception as e:
            _log.debug("import %s failed: %s", tool_name, e)

    # 2. Fallback si pas de BaseModel dans le fichier
    if model_class is None:
        model_class = _FALLBACK_MODELS.get(tool_name)

    if model_class is None:
        _log.warning("Aucun modèle pour %s — ignoré", tool_name)
        return None

    # 3. Convertir en JSON Schema
    try:
        schema = model_class.model_json_schema()
    except Exception as e:
        _log.error("schema %s failed: %s", tool_name, e)
        return None

    # 4. Formater pour OpenAI tool calling
    # Nettoyer le schema (retirer title, $defs qui perturbent certains providers)
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Nettoyer chaque propriété (garder type + description + enum)
    clean_props = {}
    for key, val in properties.items():
        clean_val = {}
        if "type" in val:
            clean_val["type"] = val["type"]
        elif "anyOf" in val:
            # Pydantic génère anyOf pour les Optional → simplifier
            clean_val["type"] = "string"
        if "description" in val:
            clean_val["description"] = val["description"]
        if "enum" in val:
            clean_val["enum"] = val["enum"]
        if "default" in val:
            clean_val["default"] = val["default"]
        if "items" in val:
            clean_val["items"] = val["items"]
        clean_props[key] = clean_val

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or f"Outil {tool_name}",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": clean_props,
                "required": required,
            },
        },
    }


class ToolRegistry:
    """Registry des schémas d'outils. Singleton initialisé au démarrage."""

    _instance: "ToolRegistry | None" = None
    _schemas: dict[str, dict[str, Any]] = {}
    _tool_names: list[str] = []

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        """Scanne tools/ et construit tous les schémas."""
        if not _TOOLS_DIR.is_dir():
            _log.warning("tools/ introuvable: %s", _TOOLS_DIR)
            return

        for f in sorted(_TOOLS_DIR.glob("*.py")):
            if f.stem == "__init__" or f.name.startswith("_"):
                continue
            schema = _build_tool_schema(f.stem)
            if schema:
                self._schemas[f.stem] = schema
                self._tool_names.append(f.stem)

        _log.info("Registry: %d outils chargés", len(self._tool_names))

    def get_schema(self, tool_name: str) -> dict[str, Any] | None:
        """Retourne le schéma OpenAI d'un outil."""
        return self._schemas.get(tool_name)

    def get_all_schemas(self) -> list[dict[str, Any]]:
        """Retourne tous les schémas (pour debug)."""
        return list(self._schemas.values())

    def get_tool_names(self) -> list[str]:
        """Retourne la liste des noms d'outils."""
        return list(self._tool_names)

    def get_filtered_names(self, enabled: list[str] | None = None) -> list[str]:
        """Retourne les noms d'outils filtrés par la whitelist."""
        if enabled is None:
            return list(self._tool_names)
        return [t for t in self._tool_names if t in enabled]

    def get_control_tools(self) -> list[dict[str, Any]]:
        """Retourne les outils de contrôle (think + final_answer)."""
        return list(_CONTROL_TOOLS)

    def build_meta_tool(self, tool_names: list[str] | None = None) -> dict[str, Any]:
        """Construit le meta-tool request_tool_definition avec l'enum des noms."""
        names = tool_names if tool_names is not None else self._tool_names
        return {
            "type": "function",
            "function": {
                "name": "request_tool_definition",
                "description": (
                    "Récupère le schéma JSON d'un outil spécifique. "
                    "Appelez cet outil quand vous avez besoin d'utiliser un outil "
                    "mais que vous ne connaissez pas ses paramètres exacts. "
                    "Le schéma retourné vous donnera tous les paramètres et leur type."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "enum": names,
                            "description": "Nom de l'outil dont vous voulez le schéma.",
                        },
                    },
                    "required": ["tool_name"],
                },
            },
        }


# --- Accès global ---

def get_registry() -> ToolRegistry:
    """Retourne le singleton ToolRegistry."""
    return ToolRegistry()