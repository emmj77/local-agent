"""
gpu_manage.py — Gestion unifiée GPU: status / up / down (fusion 3 outils).

Interface: def run(json_args: str): print(json.dumps(result))

Actions:
  status                              -> lit GPU/RAM/serveurs/modeles (LECTURE SEULE)
  up    + target=comfyui|vision|llama -> démarre un serveur GPU (idempotent)
  down  + target=comfyui|vision|llama -> arrête un serveur GPU et libère la VRAM

Args JSON:
  {"action": "status"}
  {"action": "up", "target": "comfyui"}
  {"action": "up", "target": "vision"}
  {"action": "up", "target": "llama", "model": "Qwen3-8B-Q4_K_M.gguf"}
  {"action": "down", "target": "comfyui"}
  {"action": "down", "target": "vision"}
  {"action": "down", "target": "llama"}

GPU0 = RTX 3060 (12 Go). Un serveur = un modèle chargé.
Pour llama, 'model' est requis (nom GGUF exact, vérifié contre la liste disponible).
"""
import sys
import json
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/
from core import gpu_manager as g

_TARGETS = ("comfyui", "vision", "llama")


class GpuManageArgs(BaseModel):
    action: str = Field(..., description="status | up | down")
    target: str | None = Field(None, description="comfyui | vision | llama (requis pour up/down)")
    model: str | None = Field(None, description="GGUF (requis pour llama up)")


# ── helpers status ──────────────────────────────────────────────

def _gpus() -> list[dict]:
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.used,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return []
    gpus = []
    for line in out.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 5:
            gpus.append({"id": int(p[0]), "name": p[1],
                         "vram_used_mib": int(p[2]), "vram_total_mib": int(p[3]),
                         "vram_free_mib": int(p[4])})
    return gpus


# ── action: status ──────────────────────────────────────────────

def _status():
    result = {
        "status": "success",
        "gpus": _gpus(),
        "ram_free_mib": g.sys_ram_free_mib(),
        "servers": {
            "comfyui": {"port": 8188, "up": g.comfyui_running()},
            "vision_vl": {"port": 1234, "up": g.vl_running()},
            "llama": {"port": 8088, "up": g.llama_running()},
        },
        "models": {
            "llama": g.list_llama_models(),
            "comfyui_checkpoints": g.list_comfyui_checkpoints(),
        },
        "note": "GPU0 = RTX 3060 (12 Go). Utilise action=up/down pour charger/décharger.",
    }
    print(json.dumps(result, ensure_ascii=False))


# ── action: up ──────────────────────────────────────────────────

def _up(target: str, model: str | None):
    if target == "comfyui":
        ok = g.start_comfyui()
        print(json.dumps({"status": "success" if ok else "error",
                          "target": "comfyui", "up": g.comfyui_running(),
                          "message": "ComfyUI prêt :8188" if ok else "ComfyUI n'a pas démarré (timeout)"},
                         ensure_ascii=False))
        return

    if target == "vision":
        ok = g.ensure_vl()
        print(json.dumps({"status": "success" if ok else "error",
                          "target": "vision", "up": g.vl_running(),
                          "message": "Serveur vision Qwen3-VL prêt :1234" if ok else "VL n'a pas démarré (timeout)"},
                         ensure_ascii=False))
        return

    # llama : modèle obligatoire + nom EXACT
    models = g.list_llama_models()
    if not model:
        print(json.dumps({"status": "error", "target": "llama",
                          "message": "model requis pour llama.", "modeles_disponibles": models},
                         ensure_ascii=False))
        return
    if model not in models:
        print(json.dumps({"status": "error", "target": "llama",
                          "message": f"modèle inconnu: {model}", "modeles_disponibles": models},
                         ensure_ascii=False))
        return
    ok = g.start_llama(model)
    print(json.dumps({"status": "success" if ok else "error", "target": "llama",
                      "model": model, "up": g.llama_running(),
                      "message": f"llama.cpp :8088 prêt ({model})" if ok else "llama n'a pas démarré (timeout/VRAM ?)"},
                     ensure_ascii=False))


# ── action: down ────────────────────────────────────────────────

def _down(target: str):
    if target == "comfyui":
        g.stop_comfyui()
        up = g.comfyui_running()
    elif target == "vision":
        g.stop_vl()
        up = g.vl_running()
    else:  # llama
        g.stop_llama()
        up = g.llama_running()

    print(json.dumps({"status": "success" if not up else "error",
                      "target": target, "up": up,
                      "gpu0_free_mib": g.gpu_free_mib(0),
                      "message": f"{target} arrêté" if not up else f"{target} encore actif"},
                     ensure_ascii=False))


# ── point d'entrée ──────────────────────────────────────────────

def run(json_args: str):
    try:
        a = GpuManageArgs.model_validate_json(json_args)
    except ValidationError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return

    action = a.action.strip().lower()

    if action == "status":
        _status()
        return

    if action in ("up", "down"):
        if not a.target:
            print(json.dumps({"status": "error",
                              "message": f"target requis pour action={action}. Attendu: {list(_TARGETS)}"}))
            return
        target = a.target.strip().lower()
        if target not in _TARGETS:
            print(json.dumps({"status": "error",
                              "message": f"target inconnu: {a.target}. Attendu: {list(_TARGETS)}"}))
            return
        if action == "up":
            _up(target, a.model)
        else:
            _down(target)
        return

    print(json.dumps({"status": "error",
                      "message": f"action inconnue: {a.action}. Attendu: status | up | down"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")