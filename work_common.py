"""Shared Ollama and result helpers for independent picture works."""

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

try:
    from PIL import Image
    import pillow_heif

    pillow_heif.register_heif_opener()
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# Extensions Ollama's vision loader cannot decode natively and that we
# therefore transcode to JPEG in memory before sending.
_REENCODE_EXTS = {".heic", ".heif"}


def _load_image_bytes(source_path):
    """Return sendable image bytes for one source file.

    HEIC/HEIF images are transcoded to JPEG in memory (Ollama's vision
    endpoint only decodes JPEG/PNG). Everything else is passed through
    unchanged. Empty (0-byte) files are rejected up front with a clear
    message instead of producing an opaque HTTP 400 downstream.
    """
    if os.path.getsize(source_path) == 0:
        raise RuntimeError(
            "source file is empty (0 bytes) -- likely a failed iCloud/export download")

    ext = os.path.splitext(source_path)[1].lower()
    if ext in _REENCODE_EXTS:
        if not _PIL_AVAILABLE:
            raise RuntimeError("HEIC conversion failed: install pillow-heif")
        try:
            image = Image.open(source_path).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue()
        except Exception as exc:
            raise RuntimeError("HEIC conversion failed: %s" % exc) from exc

    with open(source_path, "rb") as source:
        return source.read()


def ollama_post_json(base_url, endpoint, payload, timeout=180):
    """POST JSON to Ollama and return its decoded response."""
    request = urllib.request.Request(
        "%s%s" % (base_url, endpoint),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        raise RuntimeError("Ollama %s failed: %s" % (endpoint, exc)) from exc


def vision_request(source_path, prompt, config):
    """Send one image to Ollama's vision endpoint."""
    encoded = base64.b64encode(_load_image_bytes(source_path)).decode("ascii")
    response = ollama_post_json(config["ollama_base"], "/api/generate", {
        "model": config["vision_model"],
        "prompt": prompt,
        "stream": False,
        "images": [encoded],
    })
    result = response.get("response", "")
    if not result:
        raise RuntimeError("vision returned no response")
    return result.strip()


def parse_json_response(text):
    """Parse a JSON object even when a model adds a markdown fence."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())


def result_record(source, output=None, error=None):
    """Return the minimal per-source work result envelope."""
    if error:
        output = {"error": str(error)}
    return {"source_key": source["source_key"], "output": output}
