"""image_scale.py — Outil agent : redimensionne une image (agrandir/réduire).

Redimensionnement géométrique local via Pillow (LANCZOS, pas de GPU). Pour un
upscale IA (ESRGAN), voir un outil ComfyUI dédié.

Args JSON (fournir SOIT scale, SOIT width/height) :
  {"image_path": "/x.png", "scale": 2.0}                 -> x2 (agrandir)
  {"image_path": "/x.png", "scale": 0.5}                 -> /2 (réduire)
  {"image_path": "/x.png", "width": 1024}                -> largeur 1024, hauteur auto (ratio gardé)
  {"image_path": "/x.png", "width": 800, "height": 600}  -> dimensions exactes
  {"image_path": "...", "output_dir": "/home/moussa/Bureau"}

R6 (run(json_args)), R9.
"""
import sys
import json
import time
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from PIL import Image


class ScaleArgs(BaseModel):
    image_path: str = Field(..., description="Image d'entrée")
    scale: float = Field(default=0.0, description="Facteur d'échelle (>0). Ignoré si width/height fournis.")
    width: int = Field(default=0, description="Largeur cible (0 = auto)")
    height: int = Field(default=0, description="Hauteur cible (0 = auto)")
    output_dir: str = Field(default="/home/moussa/Bureau", description="Dossier de sortie")


def _target_size(w: int, h: int, a: ScaleArgs) -> tuple[int, int]:
    if a.width or a.height:                       # dimensions cibles (ratio gardé si un seul donné)
        if a.width and a.height:
            return a.width, a.height
        if a.width:
            return a.width, max(1, round(h * a.width / w))
        return max(1, round(w * a.height / h)), a.height
    if a.scale > 0:                               # facteur
        return max(1, round(w * a.scale)), max(1, round(h * a.scale))
    raise ValueError("Fournir 'scale' (>0) OU 'width'/'height'.")


def run(json_args: str):
    try:
        a = ScaleArgs.model_validate_json(json_args)
    except ValidationError as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return
    try:
        src = Path(a.image_path)
        if not src.exists():
            print(json.dumps({"status": "error", "message": f"Image introuvable: {a.image_path}"}))
            return
        with Image.open(src) as im:
            orig_w, orig_h = im.width, im.height
            tw, th = _target_size(orig_w, orig_h, a)
            out_im = im.resize((tw, th), Image.Resampling.LANCZOS)
            out_dir = Path(a.output_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{src.stem}_scaled_{tw}x{th}{src.suffix or '.png'}"
            out_im.save(out_path)
        print(json.dumps({"status": "success", "image_path": str(out_path),
                          "from": f"{orig_w}x{orig_h}", "to": f"{tw}x{th}",
                          "message": f"Image redimensionnée: {out_path}"}, ensure_ascii=False))
    except (ValueError, OSError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
