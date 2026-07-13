# Local Agent — v22

Agent local multi-instances avec architecture think/final_answer + tool_choice="required".

## Architecture

- **think / final_answer**: Le LLM ne peut générer que des appels d'outil. Zéro texte brut.
- **tool_choice="required"**: Force l'API à valider la structure avant de répondre.
- **Meta-tool**: Lazy loading des schémas d'outils (le LLM demande le schéma avant d'utiliser un outil).
- **Fichiers plats**: Plus de SQLite ni ChromaDB. Sessions .md, logs .log JSONL, mémoire .md + index JSON.

## Structure

```
agent/
  agent_loop.py          # Boucle principale (think/final_answer/meta-tool)
  config.py              # Configuration (InstanceConfig, GlobalConfig)
  core/
    router.py            # Routeur d'outils (importlib tools/*.py)
    session_manager.py   # Sessions en .md + index JSON
    log_manager.py       # Logs en .log JSONL
    gpu_manager.py       # Gestion GPU (comfyui/llama/vision)
    templates/           # Templates Jinja2 (project, skill, cron, todo)
  llm/
    instructor_guard.py  # Parsing tool_calls + ToolAction (is_control, is_meta)
    tool_registry.py     # Registry + schémas OpenAI (strict:true)
    llm_client.py        # Client LLM multi-endpoints
  tools/                 # 19 outils appelables par le LLM
    file_manage.py       # read/write/append/delete/metadata/list/search/grep
    gpu_manage.py        # status/up/down
    comfyui_manage.py    # generate/upscale
    network_manage.py    # get/download
    system_manage.py     # status/tokens
    project_manage.py    # list/view/add/rapport
    memory_manage.py     # add/read/list/search
    session_manage.py    # search/summary
    skill_manage.py      # list/view/add
    todo_manage.py       # template/write/check/end/list
    cron_manage.py       # list/purge/tools/write/report
    execute_terminal.py  # Terminal
    codegraph_explore.py # CodeGraph MCP
    mcp_call.py          # MCP générique
    sqlite_query.py      # DB_CENTRALE (lecture seule)
    triposr_3d.py        # 3D
    vision.py            # Vision VL
    image_scale.py       # Redimensionnement image
    log_view.py          # Lecture logs
  dashboard/             # Dashboard Streamlit
    chat.py              # Chat avec think/final_answer/timer/compteur
    pages.py             # 7 pages (Serveurs, Skills, Sessions, Mémoire, Cron, Logs, Outils)
    styles.py            # CSS + markdown rendu
    config_agents.py     # Config + Redis (@st.cache_resource)
  bus/
    redis_router.py      # BRPOP + pause/resume
    redis_publisher.py   # PUBLISH events
    redis_task_queue.py  # Background tasks
```

## Démarrage

```bash
bash agent/start_LA.sh
```

## Outils de contrôle (injected à chaque tour)

- `think(thought_process)` — Réfléchir, planifier, analyser
- `final_answer(response)` — Réponse finale à l'utilisateur
- `request_tool_definition(tool_name)` — Découvrir le schéma d'un outil