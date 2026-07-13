"""comfyui_manage.py — Outil agent unifié : generate (txt2img/img2img/Flux) + upscale.

Fusion de comfyui_generate.py et comfyui_upscale.py en un seul point d'entrée.
L'argument ``action`` (``generate`` | ``upscale``) détermine le workflow ComfyUI.

Actions:
  - **generate** : text-to-image, img2img (si image_path fourni), ou Flux (si
    checkpoint contient "flux").  Cf. comfyui_generate.py d'origine.
  - **upscale** : agrandissement IA via modèle ESRGAN/OmniSR.
    Cf. comfyui_upscale.py d'origine.

Nécessite ComfyUI :8188 (server_up target:comfyui, server_down après pour libérer VRAM).

Args JSON (generate, txt2img) :
  {"action": "generate", "prompt": "un chat noir sur un toit au coucher du soleil"}
  {"action": "generate", "prompt": "...", "checkpoint": "dreamshaper_8.safetensors",
   "width": 768, "height": 768, "steps": 25, "cfg": 7.0, "sampler": "euler",
   "seed": -1, "negative": "...", "output_dir": "/home/moussa/Bureau"}

Args JSON (generate, img2img) : ajouter image_path + denoise :
  {"action": "generate", "image_path": "/x.png", "prompt": "aquarelle, doux", "denoise": 0.6}

Args JSON (generate, Flux) :
  {"action": "generate", "prompt": "photo, homme brun, chemise lin, studio, 85mm, bokeh",
   "checkpoint": "flux1-dev-fp8.safetensors", "guidance": 3.5, "steps": 20,
   "width": 1024, "height": 1024, "sampler": "euler"}

Args JSON (upscale) :
  {"action": "upscale", "image_path": "/x.png"}
  {"action": "upscale", "image_path": "...", "model": "4x-UltraSharp.pth",
   "output_dir": "/home/moussa/Bureau"}

R6 (run(json_args)), R9.
"""
import sys
import json
import time
import random
import uuid
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError

_API = "http://127.0.0.1:8188"


# ---------------------------------------------------------------------------
# Modèle d'arguments unifié (tous les champs des deux outils)
# ---------------------------------------------------------------------------

class ComfyuiManageArgs(BaseModel):
    # --- Commun ---
    action: str = Field(..., description="Action à exécuter: generate ou upscale")
    image_path: str = Field(default="", description="Image d'entrée (img2img pour generate, obligatoire pour upscale)")
    output_dir: str = Field(default="/home/moussa/Bureau")

    # --- generate ---
    prompt: str = Field(default="", description="Description de l'image (positif) — requis pour generate")
    checkpoint: str = Field(default="dreamshaper_8.safetensors")
    negative: str = Field(default="ugly, blurry, low quality, deformed, watermark, text")
    width: int = 768
    height: int = 768
    steps: int = 25
    cfg: float = 7.0
    sampler: str = "euler"
    seed: int = -1
    denoise: float = 0.6
    guidance: float = 3.5

    # --- upscale ---
    model: str = Field(default="4x-UltraSharp.pth", description="Modèle d'upscale (ESRGAN/OmniSR)")


# ---------------------------------------------------------------------------
# Helpers partagés (identiques dans les deux fichiers d'origine)
# ---------------------------------------------------------------------------

def _upload_image(path: Path) -> str:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n".encode()
        + f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode()
        + b"Content-Type: application/octet-stream\r\n\r\n" + path.read_bytes() + b"\r\n"
        + f"--{boundary}\r\n".encode()
        + b'Content-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
        + f"--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(f"{_API}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode())
    sub, name = res.get("subfolder", ""), res.get("name", path.name)
    return f"{sub}/{name}" if sub else name


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(f"{_API}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _get(path: str) -> bytes:
    with urllib.request.urlopen(f"{_API}{path}", timeout=30) as r:
        return r.read()


# ---------------------------------------------------------------------------
# generate : txt2img / img2img / Flux  (ex-comfyui_generate.py)
# ---------------------------------------------------------------------------

def _is_flux(a: ComfyuiManageArgs) -> bool:
    return "flux" in a.checkpoint.lower()


def _workflow_generate(a: ComfyuiManageArgs) -> tuple[dict, int]:
    seed = a.seed if a.seed >= 0 else random.randint(0, 2**31)
    clip_key = "clip"  # default SD/SDXL ; Flux fp8 l'expose aussi sous "clip"
    wf = {"4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": a.checkpoint}}}

    # --- Txt2img ou Flux (pas d'image d'entrée) ---
    if not a.image_path:
        positive = {"class_type": "CLIPTextEncode", "inputs": {"text": a.prompt, clip_key: ["4", 1]}}
        if _is_flux(a):
            wf["26"] = {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": a.guidance}}
            negative = {"class_type": "CLIPTextEncode", "inputs": {"text": "", clip_key: ["4", 1]}}
            latent = {"class_type": "EmptySD3LatentImage", "inputs": {"width": a.width, "height": a.height, "batch_size": 1}}
            cfg, scheduler, max_poll = 1.0, "simple", 300
        else:
            negative = {"class_type": "CLIPTextEncode", "inputs": {"text": a.negative, clip_key: ["4", 1]}}
            latent = {"class_type": "EmptyLatentImage", "inputs": {"width": a.width, "height": a.height, "batch_size": 1}}
            cfg, scheduler, max_poll = a.cfg, "normal", 180
        wf["6"] = positive; wf["7"] = negative; wf["5"] = latent
        pos_ref, neg_ref, lat_ref = (["26", 0] if _is_flux(a) else ["6", 0]), ["7", 0], ["5", 0]
        denoise = 1.0

    # --- Img2img (image_path fourni, pas flux) ---
    else:
        ref = _upload_image(Path(a.image_path))
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": ref}}
        wf["11"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}}
        wf["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": a.prompt, clip_key: ["4", 1]}}
        wf["7"] = {"class_type": "CLIPTextEncode", "inputs": {"text": a.negative, clip_key: ["4", 1]}}
        pos_ref, neg_ref, lat_ref = ["6", 0], ["7", 0], ["11", 0]
        cfg, scheduler, max_poll = a.cfg, "normal", 180
        denoise = a.denoise

    wf["3"] = {"class_type": "KSampler", "inputs": {
        "seed": seed, "steps": a.steps, "cfg": cfg, "sampler_name": a.sampler,
        "scheduler": scheduler, "denoise": denoise,
        "model": ["4", 0], "positive": pos_ref, "negative": neg_ref, "latent_image": lat_ref}}
    wf["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}}
    wf["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "LA_gen", "images": ["8", 0]}}
    return wf, max_poll


def _do_generate(a: ComfyuiManageArgs) -> str:
    wf, max_poll = _workflow_generate(a)
    pid = _post("/prompt", {"prompt": wf})["prompt_id"]
    images = None
    for _ in range(max_poll):
        hist = json.loads(_get(f"/history/{pid}").decode())
        if pid in hist and "9" in hist[pid].get("outputs", {}) and hist[pid]["outputs"]["9"].get("images"):
            images = hist[pid]["outputs"]["9"]["images"]
            break
        time.sleep(1)
    if not images:
        raise RuntimeError("ComfyUI n'a pas produit d'image (timeout).")
    img = images[0]
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
    blob = _get(f"/view?{q}")
    out_dir = Path(a.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"comfyui_{int(time.time())}.png"
    out_path.write_bytes(blob)
    return str(out_path)


# ---------------------------------------------------------------------------
# upscale : ESRGAN / OmniSR  (ex-comfyui_upscale.py)
# ---------------------------------------------------------------------------

def _workflow_upscale(image_ref: str, model: str) -> dict:
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": image_ref}},
        "11": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "12": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["11", 0], "image": ["10", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "LA_up", "images": ["12", 0]}},
    }


def _do_upscale(a: ComfyuiManageArgs) -> str:
    ref = _upload_image(Path(a.image_path))
    pid = _post("/prompt", {"prompt": _workflow_upscale(ref, a.model)})["prompt_id"]
    images = None
    for _ in range(180):
        hist = json.loads(_get(f"/history/{pid}").decode())
        if pid in hist and "9" in hist[pid].get("outputs", {}) and hist[pid]["outputs"]["9"].get("images"):
            images = hist[pid]["outputs"]["9"]["images"]
            break
        time.sleep(1)
    if not images:
        raise RuntimeError("ComfyUI n'a pas produit d'image (timeout).")
    img = images[0]
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
    blob = _get(f"/view?{q}")
    out_dir = Path(a.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"upscaled_{int(time.time())}.png"
    out_path.write_bytes(blob)
    return str(out_path)


# ---------------------------------------------------------------------------
# Point d'entrée unique
# ---------------------------------------------------------------------------

def run(json_args: str):
    try:
        a = ComfyuiManageArgs.model_validate_json(json_args)
    except ValidationError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return
    try:
        if a.action == "generate":
            if not a.prompt:
                print(json.dumps({"status": "error", "message": "generate requiert 'prompt'."}))
                return
            if a.image_path and not Path(a.image_path).exists():
                print(json.dumps({"status": "error", "message": f"Image introuvable: {a.image_path}"}))
                return
            path = _do_generate(a)
            print(json.dumps({"status": "success", "image_path": path,
                              "message": f"Image générée: {path}"}, ensure_ascii=False))

        elif a.action == "upscale":
            if not a.image_path:
                print(json.dumps({"status": "error", "message": "upscale requiert 'image_path'."}))
                return
            if not Path(a.image_path).exists():
                print(json.dumps({"status": "error", "message": f"Image introuvable: {a.image_path}"}))
                return
            path = _do_upscale(a)
            print(json.dumps({"status": "success", "image_path": path,
                              "message": f"Image upscalée ({a.model}): {path}"}, ensure_ascii=False))

        else:
            print(json.dumps({"status": "error",
                              "message": f"action inconnue: '{a.action}'. Attendu: generate|upscale."}))

    except (ValueError, KeyError, RuntimeError, OSError, urllib.error.URLError) as e:
        print(json.dumps({"status": "error",
                          "message": f"{type(e).__name__}: {e}. ComfyUI sur :8188 ? (server_up target:comfyui)"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")