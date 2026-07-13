"""core_utils — fonctions utilitaires centralisées (R22).

R22: parse_frontmatter centralisé — 1 seule implémentation.
Tous les managers (skill, project, cron) utilisent cette fonction.
DRY: supprime les implémentations dupliquées.
"""

from __future__ import annotations

import re
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse un fichier avec YAML frontmatter.

    Format:
        ---
        key: value
        list:
          - item1
          - item2
        ---
        Corps du fichier...

    Args:
        content: Contenu brut du fichier

    Returns:
        (frontmatter_dict, body_str)
        Si pas de frontmatter: ({}, content)
    """
    if not content.startswith("---"):
        return {}, content

    # Trouver le délimiteur de fin
    end_match = re.search(r"^---\s*$", content[3:], re.MULTILINE)
    if not end_match:
        return {}, content

    yaml_block = content[3:3 + end_match.start()].strip()
    body = content[3 + end_match.end():].lstrip("\n")

    # Parse YAML simple (pas de pyyaml pour rester léger)
    frontmatter: dict[str, Any] = {}
    _parse_simple_yaml(yaml_block, frontmatter)

    return frontmatter, body


def _parse_simple_yaml(yaml_str: str, result: dict[str, Any]) -> None:
    """Parse un bloc YAML simple (key: value, listes, nesting basique).

    Pour les cas complexes, utiliser yaml.safe_load sur le bloc.
    """

    if not _HAS_YAML:
        # Fallback: parse manuel basique si pyyaml n'est pas installé
        for line in yaml_str.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value:
                    if value.lower() in ("true", "false"):
                        result[key] = value.lower() == "true"
                    elif value.startswith("[") and value.endswith("]"):
                        items = [i.strip().strip("'\"") for i in value[1:-1].split(",") if i.strip()]
                        result[key] = items
                    else:
                        result[key] = value.strip("'\"")
        return

    try:
        parsed = yaml.safe_load(yaml_str)
        if isinstance(parsed, dict):
            result.update(parsed)
    except yaml.YAMLError:
        # Fallback: parse manuel basique
        for line in yaml_str.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if value:
                    # Essayer de parser les types simples
                    if value.lower() in ("true", "false"):
                        result[key] = value.lower() == "true"
                    elif value.startswith("[") and value.endswith("]"):
                        # Liste inline
                        items = [i.strip().strip("'\"") for i in value[1:-1].split(",") if i.strip()]
                        result[key] = items
                    else:
                        result[key] = value.strip("'\"")