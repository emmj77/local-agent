"""triposr_3d.py — Outil agent : image -> mesh 3D via TripoSR.

Décharge la VRAM de ComfyUI avant (offload GPU partagé). TripoSR tourne dans son
propre venv (transformers 4.47.1) et libère la VRAM en fin de run.

Args JSON:
  {"image_path": "/home/moussa/Bureau/objet.png"}
  {"image_path": "...", "output_dir": "/home/moussa/Bureau/TripoSR"}

Sortie : <output_dir>/0/mesh.obj (ouvrir avec f3d). R6, R9.
"""
import sys
import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/
from pydantic import BaseModel, Field
from gpu_manager import ensure_vram

_TRIPO = Path("/home/moussa/Applications/TripoSR")
_PY = _TRIPO / "venv/bin/python"


class Args(BaseModel):
    image_path: str = Field(..., description="Image d'entrée (png/jpg)")
    output_dir: str = Field(default="/home/moussa/Bureau/TripoSR", description="Dossier de sortie")


def run(json_args: str):
    try:
        a = Args.model_validate_json(json_args)
        if not Path(a.image_path).exists():
            print(json.dumps({"status": "error", "message": f"Image introuvable: {a.image_path}"}))
            return
        ensure_vram(0, 5000)  # offload : libère ComfyUI + arrête le serveur VL au besoin
        out = Path(a.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([str(_PY), "run.py", a.image_path, "--output-dir", str(out)],
                           cwd=str(_TRIPO), capture_output=True, text=True, timeout=300)
        mesh = out / "0" / "mesh.obj"
        if mesh.exists():
            print(json.dumps({"status": "success", "mesh": str(mesh),
                              "message": f"Mesh 3D généré : {mesh} (ouvrir avec f3d)"}))
        else:
            print(json.dumps({"status": "error", "message": (r.stderr or "échec TripoSR")[-300:]}))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "{}")
