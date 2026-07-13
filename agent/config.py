"""Configuration Local_Agent — pydantic-settings (agent unique paramétré).

Charge .env (python-dotenv) puis un YAML d'instance (--config configs/<nom>.yaml).
Crash net au démarrage si invalide (R2).

R8: Agent unique paramétré — 1 code, N configs.
R18: Tous les chemins centralisés dans PathsConfig.
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Racine du coffre dérivée du fichier (agent/config.py -> parent.parent), pas
# codée en dur -> portable. Surchargée par la var d'env VAULT_ROOT si présente.
_DEFAULT_VAULT = str(Path(__file__).resolve().parent.parent)


# --- Settings depuis .env (variables globales) ---

class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vault_root: str = _DEFAULT_VAULT
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    llamacpp_url: str = "http://localhost:8088"
    dashboard_port: int = 8000
    dashboard_host: str = "127.0.0.1"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    deepseek_api_key: str = ""
    # OmniRoute (gateway local — subagent free)
    omniroute_url: str = "http://localhost:20128"
    omniroute_api_key: str = ""
    # Clés API cloud (remplir dans .env)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    novitaai_api_key: str = ""
    google_api_key: str = ""
    kimi_api_key: str = ""
    # Nouveaux providers
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com"
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    xai_api_key: str = ""
    xai_base_url: str = "https://api.x.ai/v1"
    ollama_api_key: str = ""
    # Hugging Face + GitHub
    hf_token: str = ""
    hf_base_url: str = "https://router.huggingface.co/v1"
    github_token: str = ""


# --- Config d'une instance (YAML d'instance) ---

EndpointType = Literal[
    "cloud", "deepseek_api", "llamacpp", "cloud_free", "cloud_paid", "omniroute",
    "openrouter", "gemini", "nvidia", "mistral", "xai", "groq",
]
AgentType = Literal["standard", "local"]


class AgentConfig(BaseModel):
    """Config spécifique à une instance d'agent."""
    agent_id: str = Field(..., description="Identifiant unique (ex: agent1, agentlocal)")
    redis_key: str = Field(..., description="Key Redis pour les queues (ex: agent1)")
    model: str = Field(..., description="Modèle LLM (ex: glm-5.2, deepseek-reasoner)")
    endpoint: EndpointType = Field(..., description="Type d'endpoint")
    agent_type: AgentType = Field("standard", description="standard ou local (harnais léger)")
    name: str = Field("", description="Nom affichable")
    role: str = Field("", description="Rôle de l'agent")

    # LLM
    context_window: int = Field(32768, description="Fenêtre de contexte en tokens")
    max_system_tokens: int = Field(4000, description="Tokens max pour le system prompt")
    max_history_messages: int = Field(50, description="Messages max dans l'historique")
    max_tool_calls: int = Field(10, description="Itérations max dans la boucle")
    timeout_http: int = Field(120, description="Timeout HTTP en secondes")
    max_output_tokens: int = Field(8192, description="Tokens max en output (défaut 8192)")
    timeout_terminal: int = Field(30, description="Timeout terminal en secondes")

    # Outils (pour harnais léger — restreint la liste)
    tools_enabled: list[str] | None = Field(
        None, description="Liste des outils activés (None = tous)"
    )

    # Outils longs (task queue Redis)
    long_running_tools: list[str] = Field(
        default_factory=lambda: ["http_get_request", "download_file"]
    )
    # Timeout de blocage sur une tâche longue avant de la différer (option B)
    long_task_timeout: int = Field(60, description="Secondes d'attente bloquante d'un résultat de tâche longue")


# --- Chemins centralisés (R18) ---

class PathsConfig(BaseModel):
    vault_root: str = _DEFAULT_VAULT
    agent_dir: str = ""  # Rempli dynamiquement = dossier agent/
    skills_dir: str = ""
    projects_dir: str = ""
    sessions_dir: str = ""
    memory_dir: str = ""
    logs_dir: str = ""
    cron_dir: str = ""
    codebase_dir: str = ""
    templates_dir: str = ""  # core/templates/
    context_json: str = ""   # agent/context/context.json
    dashboard_dir: str = ""  # agent/dashboard/

    def resolve(self) -> "PathsConfig":
        """Résout tous les chemins en absolus basés sur vault_root."""
        root = Path(self.vault_root).resolve()
        agent_dir = root / "agent"
        return PathsConfig(
            vault_root=str(root),
            agent_dir=str(agent_dir),
            skills_dir=str(root / "03_SKILLS"),
            projects_dir=str(root / "02_PROJETS"),
            sessions_dir=str(root / "04_SESSIONS"),
            memory_dir=str(root / "05_MEMORY"),
            logs_dir=str(root / "06_LOGS"),
            cron_dir=str(root / "07_CRON"),
            codebase_dir=str(root / "07_CODEBASE"),
            templates_dir=str(agent_dir / "core" / "templates"),
            context_json=str(agent_dir / "context" / "context.json"),
            dashboard_dir=str(agent_dir / "dashboard"),
        )


# --- Sécurité ---

class SecurityConfig(BaseModel):
    blocked_commands: list[str] = Field(
        default_factory=lambda: [
            "rm -rf", "mkfs", "dd if=/dev/zero",
            ":(){:|:&};:", "shutdown", "reboot",
        ]
    )


# --- Config d'instance complète ---

class InstanceConfig(BaseModel):
    """Config complète d'une instance d'agent (env + agent + paths + security)."""
    env: EnvSettings = Field(default_factory=EnvSettings)
    agent: AgentConfig
    paths: PathsConfig = Field(default_factory=PathsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @classmethod
    def load(cls, config_path: str) -> "InstanceConfig":
        """Charge .env puis le YAML d'instance. Crash net si invalide.

        R21: Vérifie les permissions du .env (crash net si 644+).
        Usage:
            config = InstanceConfig.load("agent/configs/agent1.yaml")
        """
        # R21: Vérifier les permissions du .env
        _check_env_permissions()

        env = EnvSettings()

        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"Config introuvable: {config_path}")

        with open(p, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Remplace les ${VAR} par les valeurs d'environnement
        raw = _expand_vars(raw, env)

        # Le YAML d'instance contient la section agent:
        # agent:
        #   agent_id: agent1
        #   redis_key: agent1
        #   model: glm-5.2
        #   endpoint: cloud
        agent_data = raw.get("agent", {})

        # Paths héritent de vault_root depuis env
        paths_data = raw.get("paths", {})
        if "vault_root" not in paths_data:
            paths_data["vault_root"] = env.vault_root

        paths = PathsConfig(**paths_data).resolve()
        agent = AgentConfig(**agent_data)
        # context.json PAR AGENT : sinon les N instances partagent le même
        # fichier -> contamination croisée des historiques + races à l'écriture.
        paths.context_json = str(
            Path(paths.agent_dir) / "context" / f"context_{agent.agent_id}.json"
        )
        security_data = raw.get("security", {})

        return cls(
            env=env,
            agent=agent,
            paths=paths,
            security=SecurityConfig(**security_data),
        )

    @property
    def redis_queue_key(self) -> str:
        """Key Redis pour la queue de requêtes de cette instance."""
        return f"agent:{self.agent.redis_key}:queue"

    @property
    def redis_events_channel(self) -> str:
        """Channel Redis pour les Pub/Sub events de cette instance."""
        return f"agent:{self.agent.redis_key}:events"

    @property
    def redis_long_tasks_key(self) -> str:
        """Key Redis pour la task queue des outils longs."""
        return f"agent:{self.agent.redis_key}:long_tasks"


# --- Config globale (pour dashboard, telegram, watcher) ---

class GlobalAgentEntry(BaseModel):
    """Entrée d'agent dans la config globale (pour dashboard/telegram)."""
    agent_id: str
    redis_key: str
    model: str
    endpoint: EndpointType
    agent_type: AgentType = "standard"
    name: str = ""
    role: str = ""


class GlobalConfig(BaseModel):
    """Config globale — liste de tous les agents + chemins + telegram."""
    env: EnvSettings = Field(default_factory=EnvSettings)
    agents: list[GlobalAgentEntry] = Field(default_factory=list)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @classmethod
    def load(cls, config_path: str = "agent/configs/config_global.yaml") -> "GlobalConfig":
        """Charge .env puis config.yaml global. Crash net si invalide."""
        env = EnvSettings()

        p = Path(config_path)
        if not p.exists():
            return cls(env=env, paths=PathsConfig(vault_root=env.vault_root).resolve())

        with open(p, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        raw = _expand_vars(raw, env)

        agents_data = raw.get("agents", [])
        paths_data = raw.get("paths", {})
        if "vault_root" not in paths_data:
            paths_data["vault_root"] = env.vault_root

        return cls(
            env=env,
            agents=[GlobalAgentEntry(**a) for a in agents_data],
            paths=PathsConfig(**paths_data).resolve(),
            security=SecurityConfig(**raw.get("security", {})),
        )


def _expand_vars(data, env: EnvSettings):
    """Remplace ${VAR} dans les valeurs string par les vars d'environnement.

    Gère les variables INTÉGRÉES (ex: "redis://${redis_host}:6379", "${A}${B}"),
    pas seulement la valeur entière "${VAR}".
    C3 fix: utilise env.model_dump() (pas getattr) pour ne pas exposer d'attributs internes.
    """
    env_vars = env.model_dump()
    if isinstance(data, dict):
        return {k: _expand_vars(v, env) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_vars(v, env) for v in data]
    if isinstance(data, str) and "${" in data:
        def _sub(m):
            name = m.group(1).lower()
            val = env_vars.get(name)
            if val is None:
                raise ValueError(f"Variable d'environnement non trouvée: ${{{m.group(1)}}}")
            return str(val)
        return re.sub(r"\$\{(\w+)\}", _sub, data)
    return data


def _check_env_permissions() -> None:
    """R21: Vérifie que le .env a des permissions restrictives (600).

    Crash net si le .env est lisible par groupe ou autres (644+).
    """
    from pathlib import Path
    import os

    # Chercher le .env: dossier parent de agent/ (vault root)
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return  # Pas de .env — pas d'erreur (sera créé par l'utilisateur)

    mode = env_path.stat().st_mode
    perms = mode & 0o777

    # Dangereux = n'importe quel bit group/other (les permissions ne sont PAS
    # ordinales : 0o060 ou 0o006 sont < 0o600 mais lisibles par group/other).
    if perms & 0o077:
        os.chmod(env_path, 0o600)
        import logging
        logging.warning(f"[R21] .env permissions {oct(perms)} → 600 (auto-fix)")