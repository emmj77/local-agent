"""vision.py — Outil agent : analyse d'image via Qwen3-VL (llama.cpp :1234).

Autonome (client VL intégré, ex-mcp_qwen_vl.py). Modes : describe (défaut), ocr,
analyze (avec question). Démarre/charge le serveur vision (libère ComfyUI) au besoin.

Args JSON:
  {"image_path": "/home/moussa/Bureau/x.png"}
  {"image_path": "...", "mode": "ocr"}
  {"image_path": "...", "mode": "analyze", "question": "Combien de personnes ?"}

R6 (run(json_args)), R9.
"""
import sys
import json
import base64
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/
from pydantic import BaseModel, Field
from gpu_manager import ensure_vl

_LLAMA_API = "http://127.0.0.1:1234/v1"
_MODEL = "qwen3-vl-8b-instruct"
_TIMEOUT = 120
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif"}


def _image_to_base64(image_path: str) -> str:
    p = Path(image_path)
    mime = _MIME.get(p.suffix.lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def _call_vl(prompt: str, b64: str, temperature: float, max_tokens: int) -> dict:
    payload = {"model": _MODEL, "temperature": temperature, "max_tokens": max_tokens,
               "stream": False, "messages": [{"role": "user", "content": [
                   {"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": b64}}]}]}
    req = urllib.request.Request(f"{_LLAMA_API}/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            res = json.loads(r.read())
            return {"content": res.get("choices", [{}])[0].get("message", {}).get("content", ""),
                    "usage": res.get("usage", {})}
    except urllib.error.URLError as e:
        return {"error": f"llama.cpp VL injoignable (:1234): {e}"}


_PROMPTS = {
    "describe": ("Décris cette image en détail en français. Objets, personnes, couleurs, "
                 "ambiance, tout détail pertinent.", 0.3, 1024),
    "ocr": ("Extrais TOUT le texte visible dans cette image. Retourne uniquement le texte, "
            "mot pour mot, sans commentaire.", 0.1, 2048),
}


class VisionArgs(BaseModel):
    image_path: str = Field(..., description="Chemin de l'image")
    mode: str = Field(default="describe", description="describe | ocr | analyze")
    question: str = Field(default="", description="Question (mode analyze)")


def run(json_args: str):
    try:
        a = VisionArgs.model_validate_json(json_args)
        if not Path(a.image_path).exists():
            print(json.dumps({"status": "error", "message": f"Image introuvable: {a.image_path}"}))
            return
        if not ensure_vl():   # démarre le serveur vision (libère ComfyUI) si besoin
            print(json.dumps({"status": "error", "message": "Serveur vision (:1234) indisponible."}))
            return
        if a.mode == "analyze":
            prompt, temp, mx = a.question or "Décris cette image en détail.", 0.3, 1024
        else:
            prompt, temp, mx = _PROMPTS.get(a.mode, _PROMPTS["describe"])
        out = _call_vl(prompt, _image_to_base64(a.image_path), temp, mx)
        print(json.dumps({"status": "success", "mode": a.mode, "result": out}, ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError) as e:
        print(json.dumps({"status": "error",
                          "message": f"{type(e).__name__}: {e} (serveur VL sur :1234 ?)"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
