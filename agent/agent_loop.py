"""Boucle principale de l'agent — tool calling natif OpenAI + meta-tool lazy loading.

Agent unique paramétré (R8): utilise agent/config.py (InstanceConfig).
R9: Un fichier = une fonction métier (ici: orchestration de la boucle agent).

Architecture:
- Le LLM ne voit qu'un meta-tool (request_tool_definition) au départ.
- Il demande le schéma d'un outil → on l'injecte → il l'appelle.
- finish_reason="stop" sans tool_calls = fini (remplace l'ancien marqueur de fin).

Robustesse :
- CONTEXTE BORNÉ : sorties d'outils clippées + historique plafonné.
- ANTI-BOUCLE : appel (outil+args) identique répété -> alerte puis arrêt.
- Historique au format OpenAI (role=assistant + tool_calls, role=tool + result).
"""

import json
import sys
import time
from pathlib import Path

# --- Chemins d'import ---
_AGENT_DIR = Path(__file__).parent  # agent/
sys.path.insert(0, str(_AGENT_DIR))

from config import InstanceConfig
from llm.instructor_guard import parse_llm_output, get_meta_tool_schema, get_tool_schema, get_control_tools_schema
from llm.tool_registry import get_registry
from context.context_loader import load_context_files
from context.payload_builder import assemble_payload
from core.router import route_tool_call, _write_live_log as _log_live
from core.log_manager import log_local
from core.session_manager import SessionManager

import logging
_log = logging.getLogger("agent_loop")

_MAX_RESULT_CHARS = 8000   # clip d'une sortie d'outil injectée dans l'historique
_MAX_HISTORY_MSGS = 40     # plafond de messages gardés (hors system)
_LOOP_WARN = 3             # alerte anti-boucle
_LOOP_ABORT = 5            # arrêt anti-boucle


def _list_tool_names(config: InstanceConfig) -> list[str]:
    """Liste les noms d'outils disponibles via le registry, filtrés par config."""
    reg = get_registry()
    return reg.get_filtered_names(config.agent.tools_enabled)


def _clip(text: str, limit: int = _MAX_RESULT_CHARS) -> str:
    """Borne une sortie d'outil (tête + queue) pour ne pas noyer le contexte."""
    if len(text) <= limit:
        return text
    head, tail = text[: limit * 2 // 3], text[-limit // 3:]
    return f"{head}\n\n[…{len(text) - limit} caractères coupés…]\n\n{tail}"


def _normalize(msgs: list[dict]) -> list[dict]:
    """Garantit l'alternation des rôles : fusionne tout message consécutif de
    même rôle (sauf role=tool qui doit rester isolé)."""
    out: list[dict] = []
    for m in msgs:
        role = m["role"]
        # Les messages tool ne sont jamais fusionnés
        if role == "tool":
            out.append(m)
            continue
        if out and out[-1]["role"] == role and out[-1]["role"] != "tool":
            out[-1]["content"] += "\n\n" + m["content"]
        else:
            out.append({"role": role, "content": m["content"]})
    return out


class AgentLoop:
    """Boucle principale: input -> LLM -> tool_call -> execute -> reinject."""

    def __init__(self, config: InstanceConfig, publisher=None, redis_client=None):
        self.config = config
        self.agent_id = config.agent.agent_id
        self.agent_dir = str(_AGENT_DIR)
        self.tool_names = _list_tool_names(config)
        self.long_running = config.agent.long_running_tools
        self.max_tool_calls = config.agent.max_tool_calls
        self.publisher = publisher
        self.redis_client = redis_client
        self.session = SessionManager(config)
        self._registry = get_registry()

    # ── Helpers Redis (dashboard) ──

    _API_KEY_ATTR = {
        "omniroute": "omniroute_api_key",
        "openrouter": "openrouter_api_key",
        "gemini": "gemini_api_key",
        "deepseek_api": "deepseek_api_key",
        "nvidia": "nvidia_api_key",
        "mistral": "mistral_api_key",
        "xai": "xai_api_key",
        "groq": "groq_api_key",
    }

    def _get_api_key(self) -> str | None:
        ep = self.config.agent.endpoint
        if ep == "llamacpp":
            return None
        attr = self._API_KEY_ATTR.get(ep)
        if attr:
            return getattr(self.config.env, attr, None) or None
        return self.config.env.ollama_api_key or None

    def _disabled_tools(self) -> set[str]:
        if self.redis_client is None:
            return set()
        try:
            return set(self.redis_client.smembers(
                f"agent:{self.config.agent.redis_key}:tools_disabled"))
        except Exception as e:
            _log.debug("redis tools_disabled: %s", e)
            return set()

    def _model_override(self) -> str | None:
        if self.redis_client is None:
            return None
        try:
            v = self.redis_client.get(f"agent:{self.config.agent.redis_key}:model_override")
            return v or None
        except Exception as e:
            _log.debug("redis model_override: %s", e)
            return None

    def _thinking(self) -> bool:
        if self.redis_client is None:
            return False
        try:
            return bool(self.redis_client.get(f"agent:{self.config.agent.redis_key}:thinking"))
        except Exception as e:
            _log.debug("redis thinking: %s", e)
            return False

    def _stop_requested(self) -> bool:
        if self.redis_client is None:
            return False
        try:
            sk = f"agent:{self.config.agent.redis_key}:stop"
            if self.redis_client.get(sk):
                self.redis_client.delete(sk)
                return True
        except Exception:
            pass
        return False

    def _paused(self) -> bool:
        """Check si l'agent est en pause (messages en attente)."""
        if self.redis_client is None:
            return False
        try:
            return bool(self.redis_client.get(
                f"agent:{self.config.agent.redis_key}:pause"))
        except Exception:
            return False

    def _trim(self, history: list[dict]) -> list[dict]:
        """Plafonne l'historique : garde le 1er message + les N-1 derniers."""
        if len(history) <= _MAX_HISTORY_MSGS:
            return history
        return [history[0]] + history[-(_MAX_HISTORY_MSGS - 1):]

    def _finish(self, text: str, last_tool, iteration: int) -> dict:
        """Réponse finale : session + publisher + dict de retour."""
        self.session.append_message("assistant", text)
        if self.publisher:
            self.publisher.final(text, iteration)
        return {"text": text, "tool_executed": last_tool,
                "iterations": iteration, "agent_id": self.agent_id}

    def _build_tools_param(self, loaded_tool: str | None) -> list[dict]:
        """Construit la liste tools pour l'API OpenAI.

        Toujours: think + final_answer (contrôle) + meta-tool (découverte).
        Si un outil est chargé (loaded_tool): on l'ajoute aussi.
        """
        tools = get_control_tools_schema()  # think + final_answer
        tools.append(get_meta_tool_schema(self.tool_names))  # request_tool_definition
        if loaded_tool:
            schema = get_tool_schema(loaded_tool)
            if schema:
                tools.append(schema)
        return tools

    def _handle_meta_tool(self, tool_args: dict) -> str:
        """Traite request_tool_definition → retourne le schéma JSON de l'outil demandé."""
        requested = tool_args.get("tool_name", "")
        schema = get_tool_schema(requested)
        if schema is None:
            return json.dumps({
                "status": "error",
                "message": f"Outil '{requested}' introuvable. Outils disponibles: {self.tool_names}",
            }, ensure_ascii=False)
        return json.dumps({
            "status": "success",
            "tool_name": requested,
            "schema": schema,
        }, ensure_ascii=False)

    # ── Boucle principale ──

    def run(self, user_input: str) -> dict:
        # 1. Contexte + SOUL (tool calling natif — plus de parser texte custom)
        context = load_context_files(self.agent_dir, self.config)
        payload = assemble_payload(context, user_input)
        disabled = self._disabled_tools()
        model_active = self._model_override() or self.config.agent.model
        system_prompt = payload["system_prompt"]
        # Instruction meta-tool: expliquer au LLM comment utiliser les outils
        system_prompt += (
            "\n\n## Outils\n"
            "RÈGLE CRITIQUE: Tu es un moteur d'action strict. Tu n'as pas le droit "
            "de générer du texte brut en dehors des outils. Chaque message que tu envoies "
            "doit être un appel d'outil.\n"
            "- think(thought_process): pour réfléchir, analyser, planifier avant d'agir.\n"
            "- final_answer(response): pour donner ta réponse finale quand la tâche est accomplie.\n"
            "- request_tool_definition(tool_name): pour découvrir le schéma d'un outil avant de l'utiliser.\n"
            f"Outils disponibles: {', '.join(self.tool_names)}."
        )
        if self._thinking():
            system_prompt += (
                "\n\n[MODE RÉFLEXION] Avant de conclure ou d'appeler un outil, raisonne "
                "étape par étape : décompose, pèse les options, puis tranche."
            )

        # 2. Historique : résultats background + demande courante
        history: list[dict] = list(payload["conversation_history"])
        if self.redis_client is not None:
            from bus.redis_task_queue import collect_results
            for r in collect_results(self.redis_client, self.config):
                history.append({"role": "user",
                                "content": f"[Terminé: {r['tool_name']}] {_clip(r['output'])}"})
        history.append({"role": "user", "content": user_input})
        self.session.append_message("user", user_input)

        # 3. Boucle outil (2 phases: meta-tool → tool)
        last_tool = None
        loaded_tool: str | None = None  # outil dont le schéma a été chargé
        call_counts: dict[str, int] = {}

        for iteration in range(1, self.max_tool_calls + 1):
            if self._stop_requested():
                return self._finish("🛑 Arrêté à la demande (/stop).", last_tool, iteration)

            history = self._trim(history)
            messages = _normalize([{"role": "system", "content": system_prompt}, *history])

            # Construire tools = meta-tool + outil chargé (if any)
            tools_param = self._build_tools_param(loaded_tool)

            try:
                response = parse_llm_output(
                    messages=messages,
                    model=model_active,
                    endpoint=self.config.agent.endpoint,
                    llamacpp_url=self.config.env.llamacpp_url,
                    api_key=self._get_api_key(),
                    timeout=self.config.agent.timeout_http,
                    max_tokens=getattr(self.config.agent, "max_output_tokens", 8192),
                    tools=tools_param,
                    tool_choice="required",
                )
            except Exception as exc:
                _log.error("parse_llm_output crash it=%d: %s", iteration, exc)
                return self._finish(f"❌ Erreur LLM : {exc}", last_tool, iteration)

            # ── 1. Outil de contrôle: think / final_answer ──
            if response.action and response.action.is_control:
                tool_name = response.action.tool_name
                tool_args = response.action.tool_args

                if tool_name == "think":
                    thought = tool_args.get("thought_process", "")
                    print(f"[Pensée de l'agent] : {thought}")
                    log_local(f"[{self.agent_id}] [Pensée de l'agent] : {thought}", "", self.config)
                    if self.publisher:
                        self.publisher.tool_start("think", tool_args)
                        self.publisher.tool_result("think", thought, True)
                    history.append({"role": "assistant",
                                    "content": f"Pensée : {thought}"})
                    history.append({"role": "user",
                                    "content": "[SYSTEM] Réflexion enregistrée. Passe à l'action suivante."})
                    continue

                elif tool_name == "final_answer":
                    final_text = tool_args.get("response", "")
                    log_local(f"[{self.agent_id}] [Final] : {final_text[:500]}", "", self.config)
                    return self._finish(final_text, last_tool, iteration)

            # Avec tool_choice="required", response.action ne peut plus être None.
            # Sécurité: si ça arrive quand même (API non conforme), on sort.
            if response.action is None:
                return self._finish(
                    response.text or "[Erreur: aucun appel d'outil reçu]",
                    last_tool, iteration)

            tool_name = response.action.tool_name
            tool_args = response.action.tool_args

            # ── Meta-tool : request_tool_definition ──
            if response.action.is_meta:
                schema_result = self._handle_meta_tool(tool_args)
                requested_tool = tool_args.get("tool_name", "")
                # Charger le schéma pour le prochain tour
                if get_tool_schema(requested_tool):
                    loaded_tool = requested_tool
                # Injecter dans l'historique au format tool calling
                history.append({"role": "assistant", "content": response.text or f"Je demande le schéma de {requested_tool}."})
                history.append({"role": "user", "content": f"[Schéma de {requested_tool}] {schema_result}"})
                if self.publisher:
                    self.publisher.tool_start("request_tool_definition", tool_args)
                    self.publisher.tool_result("request_tool_definition", schema_result, True)
                continue

            # ── Outil réel ──
            # Anti-boucle : appel identique répété
            call_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True)}"
            call_counts[call_key] = call_counts.get(call_key, 0) + 1
            n = call_counts[call_key]
            if n >= _LOOP_ABORT:
                return self._finish(
                    f"🛑 Boucle détectée : '{tool_name}' appelé {n}× à l'identique. "
                    f"Arrêt. Reformule ou découpe la demande.", last_tool, iteration)

            # L'action entre dans l'historique
            history.append({"role": "assistant", "content": response.text or f"J'exécute {tool_name}."})
            if self.publisher:
                self.publisher.tool_start(tool_name, tool_args)

            if self._stop_requested():
                return self._finish("🛑 Arrêté à la demande (/stop).", last_tool, iteration)

            # Timer + exécution
            t0 = time.time()
            # Outil désactivé (toggle dashboard)
            if tool_name in disabled:
                result_str = f"[Outil '{tool_name}' désactivé — non exécuté]"
                success = False
            # Outil long → background
            elif tool_name in self.long_running and self.redis_client is not None:
                from bus.redis_task_queue import launch_background
                task_id = launch_background(
                    tool_name, tool_args, self.redis_client, self.config, route_tool_call)
                result_str = f"[Lancé en arrière-plan: {tool_name} #{task_id}]"
                success = True
            # Outil normal → synchrone
            else:
                result = route_tool_call(tool_name, tool_args, self.config, self.agent_id)
                result_str = _clip(result.get("output", "[erreur outil]"))
                success = result.get("success", True)
            elapsed = time.time() - t0

            last_tool = tool_name
            if self.publisher:
                self.publisher.tool_result(tool_name, result_str, success)

            # Résultat dans l'historique + alerte anti-boucle
            note = ""
            if n == _LOOP_WARN:
                note = ("\n[ALERTE BOUCLE] Tu répètes le même appel — le résultat ne "
                        "changera pas. Change d'approche ou donne ta réponse finale.")
            history.append({"role": "user", "content": f"[Résultat {tool_name}] {result_str}{note}"})

            # Log live pour dashboard: timer + compteur
            _log_live(self.agent_id, f"✓ {tool_name} ({elapsed:.1f}s) — itération {iteration}/{self.max_tool_calls}")

            # Garder loaded_tool si c'est le même outil (évite re-demander le schéma)
            if tool_name != loaded_tool:
                loaded_tool = None

        # Limite atteinte
        limit_msg = f"[LIMITE] {self.max_tool_calls} itérations sans réponse finale."
        log_local(f"Agent {self.agent_id} limit", limit_msg, self.config)
        return self._finish(limit_msg, last_tool, self.max_tool_calls)