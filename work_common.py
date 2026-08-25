"""Shared Ollama and result helpers for independent picture works."""

import base64
import json
import os
import time
import urllib.error
import urllib.request


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
    with open(source_path, "rb") as source:
        encoded = base64.b64encode(source.read()).decode("ascii")
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
