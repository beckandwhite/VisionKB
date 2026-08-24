import json
import os
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / ".workspace"
SUPPORTED_ENVIRONMENTS = (
    "DEV", "QA", "PRD-iCloud-Screenshots", "PRD-OneDrive-Pictures",
)
DEFAULT_ENV = "PRD-iCloud-Screenshots"

DEFAULT_CONFIG = {
    "ollama_base": "http://127.0.0.1:11434",
    "vision_model": "muse-glimmer:30b-mlx",
    "supported_images": ["png", "jpg", "jpeg", "heic"],
    "embed_model": "nomic-embed-text:latest",
    "source_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/",
    "temp_dir": "/tmp",
    "processed_limit": 0,
    "max_dim": 2560,
    "save_every": 25,
    "works": [
        {
            "name": "work1",
            "scope": "per_source",
            "handler": "work1",
            "output": "jsonl",
            "result_file": "work1.jsonl",
            "enabled": True,
        },
        {
            "name": "work2",
            "scope": "per_source",
            "handler": "work2",
            "output": "jsonl",
            "result_file": "work2.jsonl",
            "enabled": True,
        },
        {
            "name": "work3",
            "scope": "per_source",
            "handler": "work3",
            "output": "jsonl",
            "result_file": "work3.jsonl",
            "enabled": True,
        },
        {
            "name": "work4",
            "scope": "per_source",
            "handler": "work4",
            "output": "files",
            "output_dir": "thumbnails",
            "enabled": True,
        },
        {
            "name": "work5",
            "scope": "dataset",
            "handler": "work5",
            "output": "jsonl",
            "result_file": "duplicatefinder.jsonl",
            "enabled": True,
        },
    ],
}


def available_environments():
    """Return the configured environment names in stable display order."""
    return list(SUPPORTED_ENVIRONMENTS)


def load_config(env: str) -> tuple[Dict[str, Any], str]:
    """Load and validate one complete environment configuration."""
    env = str(env or DEFAULT_ENV)
    if env not in SUPPORTED_ENVIRONMENTS:
        choices = ", ".join(SUPPORTED_ENVIRONMENTS)
        raise ValueError("Unknown environment %r. Choose one of: %s" % (env, choices))
    config_path = WORKSPACE_DIR / env / "config.json"
    try:
        with open(config_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cannot load environment config %s: %s" %
                           (config_path, exc)) from exc
    if not isinstance(loaded, dict):
        raise RuntimeError("Environment config must be a JSON object: %s" % config_path)
    config = dict(DEFAULT_CONFIG)
    config.update(loaded)
    config["works"] = normalize_works(config.get("works"))
    if "TAG_LIST" not in config and env != "DEV":
        dev_path = WORKSPACE_DIR / "DEV" / "config.json"
        with open(dev_path, encoding="utf-8") as fh:
            dev_config = json.load(fh)
        config["TAG_LIST"] = dev_config.get("TAG_LIST", [])
    return config, env


def normalize_works(works):
    """Validate and normalize configured per-source work definitions."""
    if works is None:
        works = DEFAULT_CONFIG["works"]
    if not isinstance(works, list):
        raise ValueError("Environment config 'works' must be a list")
    normalized = []
    names = set()
    for work in works:
        if not isinstance(work, dict):
            raise ValueError("Each configured work must be an object")
        name = str(work.get("name", "")).strip()
        scope = str(work.get("scope", "per_source")).strip()
        handler = str(work.get("handler", name)).strip()
        output = str(work.get("output", "jsonl")).strip()
        if not name or name in names:
            raise ValueError("Configured work names must be non-empty and unique")
        if scope not in ("per_source", "dataset"):
            raise ValueError("Work %s has invalid scope %r" % (name, scope))
        if output not in ("jsonl", "files", "none"):
            raise ValueError("Work %s has invalid output %r" % (name, output))
        item = dict(work)
        item.update({"name": name, "scope": scope, "handler": handler,
                     "output": output, "enabled": bool(work.get("enabled", True))})
        normalized.append(item)
        names.add(name)
    return normalized


def resolve_environment(env=DEFAULT_ENV):
    """Return config plus all environment-owned artifact paths."""
    config, env = load_config(env)
    env_dir = WORKSPACE_DIR / env
    config["env_dir"] = env_dir
    config["tracker_path"] = env_dir / "_tracker.json"
    config["annotations_path"] = env_dir / "_annotations.jsonl"
    config["data_dir"] = env_dir
    config["db_path"] = env_dir / "wiki.db"
    config["exports_dir"] = env_dir
    config["thumbnails_dir"] = env_dir / "thumbnails"
    config["source_dir"] = os.path.expanduser(str(config["source_dir"]))
    config["temp_dir"] = os.path.expanduser(str(config["temp_dir"]))
    return env, config


CURRENT_ENV, CURRENT_CONFIG = resolve_environment()
