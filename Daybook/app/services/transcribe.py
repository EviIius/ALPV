"""Optional local speech-to-text for audio imports (e.g. voice memos, a field
recorder). Uses whichever is installed:

* mlx-whisper   (Apple Silicon, fastest)   pip install mlx-whisper
* faster-whisper (CPU / CUDA)              pip install faster-whisper

Model weights are downloaded once by those packages; audio never leaves the
machine.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MLX_MODELS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large": "mlx-community/whisper-large-v3-turbo",
}


def available_engines() -> list[str]:
    out = []
    if importlib.util.find_spec("mlx_whisper") is not None:
        out.append("mlx-whisper")
    if importlib.util.find_spec("faster_whisper") is not None:
        out.append("faster-whisper")
    return out


def make_transcriber(settings: dict):
    choice = settings["import"].get("transcribe", "auto")
    if choice == "off":
        return None
    engines = available_engines()
    if not engines:
        return None
    engine = engines[0] if choice == "auto" else (choice if choice in engines else None)
    if engine is None:
        return None
    size = settings["import"].get("whisper_model", "small")
    try:  # your glossary terms help Whisper spell acronyms and names right
        from . import glossary

        prompt = glossary.vocabulary() or None
    except Exception:  # noqa: BLE001
        prompt = None

    if engine == "mlx-whisper":
        def run(path: Path):
            import mlx_whisper

            repo = MLX_MODELS.get(size, MLX_MODELS["small"])
            try:
                res = mlx_whisper.transcribe(str(path), path_or_hf_repo=repo, initial_prompt=prompt)
            except TypeError:  # an older mlx-whisper without initial_prompt
                res = mlx_whisper.transcribe(str(path), path_or_hf_repo=repo)
            return _paragraphs([s.get("text", "") for s in res.get("segments", [])]) or res.get("text", ""), {
                "engine": "mlx-whisper", "language": res.get("language")}
        return run

    def run(path: Path):
        from faster_whisper import WhisperModel

        model = WhisperModel(size, device="auto", compute_type="int8")
        segments, info = model.transcribe(str(path), vad_filter=True, initial_prompt=prompt)
        return _paragraphs([s.text for s in segments]), {"engine": "faster-whisper", "language": info.language}
    return run


def _paragraphs(parts: list[str], per: int = 6) -> str:
    parts = [p.strip() for p in parts if p and p.strip()]
    return "\n\n".join(" ".join(parts[i:i + per]) for i in range(0, len(parts), per))
