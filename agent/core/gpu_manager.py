"""gpu_manager.py — Arbitrage VRAM du GPU partagé (RTX 3060 12 Go).

Consommateurs GPU : ComfyUI (:8188), serveur vision Qwen3-VL (:1234), TripoSR
(par-run), llama.cpp local (:8088). Ils ne tiennent pas tous ensemble sur 12 Go.
Ce module libère/charge ce qu'il faut avant chaque tâche.

- free_comfyui()      : décharge les modèles ComfyUI (POST /free), garde le serveur.
- stop_vl()/start_vl(): arrête/relance le serveur vision (libère/reprend sa VRAM).
- ensure_vl()         : garantit le serveur vision prêt (le démarre au besoin).
- ensure_vram(need)   : libère assez de VRAM (ComfyUI puis VL) avant une tâche lourde.
"""
import os
import json
import time
import socket
import subprocess
import urllib.request
from pathlib import Path

_COMFY = "http://127.0.0.1:8188"
_LLAMA = Path("/home/moussa/Applications/llama.cpp")
_VL_BIN = _LLAMA / "build_cuda/bin/llama-server"
_VL_LIBS = str(_LLAMA / "build_cuda/bin")
_VL_MODEL = "/home/moussa/LLM_GGUF/Qwen_Qwen3-VL-8B-Instruct-Q5_K_M.gguf"
_VL_MMPROJ = "/home/moussa/LLM_GGUF/mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf"
_VL_PORT = 1234
_VL_TAG = "mmproj-Qwen3VL"  # motif unique du process VL (pour pkill sûr)

# --- ComfyUI (:8188) ---
_COMFY_DIR = Path("/home/moussa/Applications/ComfyUI")
_COMFY_PY = _COMFY_DIR / "venv/bin/python3"
_COMFY_PORT = 8188
_COMFY_TAG = "main.py.*--port 8188"  # motif pkill distinct (cmdline: python3 main.py --listen ... --port 8188)

# --- llama.cpp local (:8088) — MÊME binaire que le VL, port différent ---
_GGUF_DIR = Path("/home/moussa/LLM_GGUF")
_LLAMA_PORT = 8088
_LLAMA_TAG = "llama-server.*--port 8088"  # motif pkill DISTINCT du VL (port 1234)


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def gpu_free_mib(device: int = 0) -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits",
             f"--id={device}"], capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return -1


def free_comfyui(api: str = _COMFY) -> bool:
    """Décharge les modèles ComfyUI de la VRAM (POST /free)."""
    try:
        req = urllib.request.Request(
            f"{api}/free",
            data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def vl_running() -> bool:
    return _port_open(_VL_PORT)


def stop_vl() -> None:
    """Arrête le serveur vision (libère sa VRAM)."""
    subprocess.run(["pkill", "-f", _VL_TAG], capture_output=True)
    for _ in range(12):
        if not vl_running():
            return
        time.sleep(0.5)


def start_vl() -> None:
    """Démarre le serveur vision (détaché) si absent."""
    if vl_running():
        return
    env = dict(os.environ, LD_LIBRARY_PATH=_VL_LIBS)
    subprocess.Popen(
        ["setsid", str(_VL_BIN), "-m", _VL_MODEL, "--mmproj", _VL_MMPROJ,
         "--host", "127.0.0.1", "--port", str(_VL_PORT),
         "-ngl", "99", "--tensor-split", "5,4", "-c", "4096"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)


def ensure_vl(timeout: int = 90) -> bool:
    """Garantit le serveur vision prêt (santé 200). Le démarre au besoin (après free_comfyui)."""
    if _healthy():
        return True
    free_comfyui()
    start_vl()
    for _ in range(timeout * 2):
        if _healthy():
            return True
        time.sleep(0.5)
    return False


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_VL_PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_vram(device: int = 0, need_mib: int = 6000) -> bool:
    """Libère assez de VRAM avant une tâche lourde (TripoSR) : ComfyUI puis serveur VL."""
    if gpu_free_mib(device) >= need_mib:
        return True
    free_comfyui()
    time.sleep(1.5)
    if gpu_free_mib(device) >= need_mib:
        return True
    stop_vl()
    time.sleep(1.5)
    return gpu_free_mib(device) >= need_mib


# --- RAM système ---

def sys_ram_free_mib() -> int:
    """RAM système disponible (MemAvailable) en MiB, -1 si indisponible."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


# --- ComfyUI (:8188) : serveur d'images. Le checkpoint se charge à la génération. ---

def comfyui_running() -> bool:
    return _port_open(_COMFY_PORT)


def start_comfyui(timeout: int = 90) -> bool:
    """Démarre ComfyUI détaché s'il est absent. True si prêt (ou déjà up)."""
    if comfyui_running():
        return True
    if not _COMFY_PY.exists():
        return False
    subprocess.Popen(
        ["setsid", str(_COMFY_PY), "main.py",
         "--listen", "127.0.0.1", "--port", str(_COMFY_PORT)],
        cwd=str(_COMFY_DIR), env=dict(os.environ),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(timeout * 2):
        if comfyui_running():
            return True
        time.sleep(0.5)
    return False


def stop_comfyui() -> None:
    """Arrête le serveur ComfyUI (libère la VRAM)."""
    subprocess.run(["pkill", "-f", _COMFY_TAG], capture_output=True)
    for _ in range(12):
        if not comfyui_running():
            return
        time.sleep(0.5)


def list_comfyui_checkpoints() -> list[str]:
    """Noms EXACTS des checkpoints ComfyUI disponibles (pour éviter les faux noms)."""
    d = _COMFY_DIR / "models" / "checkpoints"
    if not d.exists():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix in (".safetensors", ".ckpt"))


# --- llama.cpp local (:8088) : le modèle est chargé AU LANCEMENT (argument -m). ---

def _llama_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_LLAMA_PORT}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def llama_running() -> bool:
    return _port_open(_LLAMA_PORT)


def start_llama(model: str, timeout: int = 120) -> bool:
    """Démarre llama-server :8088 avec le GGUF `model` (nom de fichier dans ~/LLM_GGUF).
    Le modèle est chargé au lancement. True si santé 200 (ou déjà up)."""
    if llama_running():
        return True
    model_path = _GGUF_DIR / model
    if not Path(str(_VL_BIN)).exists() or not model_path.exists():
        return False
    env = dict(os.environ, LD_LIBRARY_PATH=_VL_LIBS, CUDA_VISIBLE_DEVICES="0")
    subprocess.Popen(
        ["setsid", str(_VL_BIN), "-m", str(model_path),
         "--host", "127.0.0.1", "--port", str(_LLAMA_PORT),
         "--ctx-size", "32768", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0",
         "--reasoning", "off", "-ngl", "99"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(timeout * 2):
        if _llama_healthy():
            return True
        time.sleep(0.5)
    return False


def stop_llama() -> None:
    """Arrête llama-server :8088 UNIQUEMENT (motif port 8088, ne touche pas le VL :1234)."""
    subprocess.run(["pkill", "-f", _LLAMA_TAG], capture_output=True)
    for _ in range(12):
        if not llama_running():
            return
        time.sleep(0.5)


def list_llama_models() -> list[str]:
    """Noms EXACTS des GGUF disponibles pour llama.cpp (hors mmproj)."""
    if not _GGUF_DIR.exists():
        return []
    return sorted(f.name for f in _GGUF_DIR.glob("*.gguf")
                  if not f.name.lower().startswith("mmproj"))


if __name__ == "__main__":
    print("comfyui freed:", free_comfyui(), "| GPU0 free:", gpu_free_mib(0),
          "MiB | VL up:", vl_running())
