"""voice.py — STT (whisper.cpp) + TTS (Piper) locaux.

transcribe(audio) : audio (oga/mp3/wav) -> texte (whisper.cpp, FR).
synthesize(text)  : texte -> WAV (Piper, voix fr_FR-siwis-medium).

Binaries dans ~/Applications (whisper.cpp, piper). R9.
"""
import os
import subprocess
import tempfile
from pathlib import Path

_WHISPER = Path("/home/moussa/Applications/whisper.cpp")
_WHISPER_BIN = _WHISPER / "build/bin/whisper-cli"
_WHISPER_MODEL = _WHISPER / "models/ggml-base.bin"
_WHISPER_LIBS = f"{_WHISPER}/build/src:{_WHISPER}/build/ggml/src"

_PIPER_BIN = Path("/home/moussa/Applications/piper/piper/piper")
_PIPER_VOICE = Path("/home/moussa/Applications/piper/fr_FR-tom-medium.onnx")


def transcribe(audio_path: str, lang: str = "fr") -> str:
    """Transcrit un fichier audio en texte via whisper.cpp."""
    wav = f"{audio_path}.16k.wav"
    subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav],
                   capture_output=True, timeout=60)
    try:
        env = dict(os.environ, LD_LIBRARY_PATH=_WHISPER_LIBS)
        r = subprocess.run([str(_WHISPER_BIN), "-m", str(_WHISPER_MODEL),
                            "-f", wav, "-l", lang, "-nt"],
                           capture_output=True, text=True, timeout=180, env=env)
        return r.stdout.strip()
    finally:
        if os.path.exists(wav):
            os.unlink(wav)


def synthesize(text: str, out_path: str | None = None) -> str:
    """Synthétise du texte en WAV via Piper. Retourne le chemin du WAV."""
    out = out_path or tempfile.mktemp(suffix=".wav")
    subprocess.run([str(_PIPER_BIN), "--model", str(_PIPER_VOICE), "--output_file", out],
                   input=text, capture_output=True, text=True, timeout=120)
    return out


def synthesize_ogg(text: str) -> str:
    """Synthétise en .ogg/opus (format vocal Telegram)."""
    wav = synthesize(text)
    ogg = wav.replace(".wav", ".ogg")
    subprocess.run(["ffmpeg", "-y", "-i", wav, "-c:a", "libopus", "-b:a", "32k", ogg],
                   capture_output=True, timeout=60)
    if os.path.exists(wav):
        os.unlink(wav)
    return ogg


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(transcribe(sys.argv[1]))
