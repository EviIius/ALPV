"""On-device text recognition for images and scanned PDFs.

Engines, in order of preference for "auto":
1. Apple Vision (macOS, via the optional pyobjc-framework-Vision package)
2. Tesseract (if the `tesseract` command is installed)
3. A local vision model through your local AI server (Settings → Local AI)

Nothing is uploaded anywhere.
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def vision_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import Vision  # noqa: F401
        import Foundation  # noqa: F401
        return True
    except Exception:
        return False


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_apple_vision(data: bytes) -> str:
    import Foundation
    import Vision

    ns = Foundation.NSData.dataWithBytes_length_(data, len(data))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    ok, _err = handler.performRequests_error_([request], None)
    if not ok:
        return ""
    lines = []
    for obs in request.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        box = obs.boundingBox()
        # Vision's origin is bottom-left; sort top-to-bottom, then left-to-right.
        lines.append((round(1 - (box.origin.y + box.size.height), 2), box.origin.x, str(cands[0].string())))
    lines.sort(key=lambda t: (t[0], t[1]))
    return "\n".join(t[2] for t in lines)


def ocr_tesseract(data: bytes, filename: str = "image.png") -> str:
    suffix = Path(filename).suffix or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        try:
            res = subprocess.run(["tesseract", f.name, "stdout"], capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return res.stdout.decode("utf-8", "ignore") if res.returncode == 0 else ""


def ocr_local_model(data: bytes, settings: dict) -> str:
    from .ai import AIClient, run_sync

    model = settings["ai"].get("vision_model")
    if not model:
        return ""
    client = AIClient.from_settings(settings)
    prompt = "Transcribe all of the text in this image exactly as written. Output only the text, keeping line breaks."
    return run_sync(client.chat(
        [{"role": "user", "content": prompt, "images": [base64.b64encode(data).decode()]}],
        model=model, temperature=0.0,
    ))


def engine_status(settings: dict) -> dict:
    return {
        "apple_vision": vision_available(),
        "tesseract": tesseract_available(),
        "local_model": bool(settings["ai"].get("enabled") and settings["ai"].get("vision_model")),
        "selected": settings["import"].get("ocr", "auto"),
    }


def make_ocr(settings: dict):
    """Return a callable (bytes, filename) -> text, or None if OCR is off/unavailable."""
    choice = settings["import"].get("ocr", "auto")
    if choice == "off":
        return None
    engines = []
    if choice in ("auto", "vision") and vision_available():
        engines.append(lambda d, n: ocr_apple_vision(d))
    if choice in ("auto", "tesseract") and tesseract_available():
        engines.append(ocr_tesseract)
    if choice in ("auto", "ai") and settings["ai"].get("enabled") and settings["ai"].get("vision_model"):
        engines.append(lambda d, n: ocr_local_model(d, settings))
    if not engines:
        return None

    def run(data: bytes, filename: str = "image") -> str:
        for engine in engines:
            try:
                text = engine(data, filename)
                if text and text.strip():
                    return text
            except Exception:
                continue
        return ""

    return run
