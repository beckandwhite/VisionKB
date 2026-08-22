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
    if "TAG_LIST" not in config and env != "DEV":
        dev_path = WORKSPACE_DIR / "DEV" / "config.json"
        with open(dev_path, encoding="utf-8") as fh:
            dev_config = json.load(fh)
        config["TAG_LIST"] = dev_config.get("TAG_LIST", [])
    return config, env


def resolve_environment(env=DEFAULT_ENV):
    """Return config plus all environment-owned artifact paths."""
    config, env = load_config(env)
    env_dir = WORKSPACE_DIR / env
    config["env_dir"] = env_dir
    config["tracker_path"] = env_dir / "_tracker.json"
    config["annotations_path"] = env_dir / "_annotations.jsonl"
    config["data_dir"] = env_dir / "data"
    config["db_path"] = env_dir / "data" / "wiki.db"
    config["exports_dir"] = env_dir / "exports"
    config["thumbnails_dir"] = env_dir / "exports" / "thumbnails"
    config["source_dir"] = os.path.expanduser(str(config["source_dir"]))
    config["temp_dir"] = os.path.expanduser(str(config["temp_dir"]))
    return env, config


CURRENT_ENV, CURRENT_CONFIG = resolve_environment()
