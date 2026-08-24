import json
import os
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / ".workspace"
TEMPLATE_PATH = WORKSPACE_DIR / "config.template.json"
ROOT_CONFIG_PATH = WORKSPACE_DIR / "config.json"

# The default environment is the ``.workspace/`` directory itself. Omitting
# ``-env`` selects it; the empty string is the sentinel that maps to the root.
ROOT_ENV = ""
DEFAULT_ENV = ROOT_ENV


def load_defaults() -> Dict[str, Any]:
    """Load the canonical template — the single source of truth for every config."""
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise RuntimeError("Config template %s must be a JSON object" % TEMPLATE_PATH)
    return dict(loaded)


def env_dir(env: str) -> Path:
    """Map an environment name to its directory; the root sentinel is the default."""
    env = str(env or ROOT_ENV)
    if env == ROOT_ENV:
        return WORKSPACE_DIR
    return WORKSPACE_DIR / env


def config_path_for(env: str) -> Path:
    """Map an environment name to its config.json path."""
    env = str(env or ROOT_ENV)
    if env == ROOT_ENV:
        return ROOT_CONFIG_PATH
    return WORKSPACE_DIR / env / "config.json"


def available_environments() -> list:
    """Named environments: immediate subfolders of .workspace/ with a config.json.

    The root default is always available but is selected by omitting ``-env``;
    it is not listed, so a bare list of subfolders is returned sorted."""
    names = []
    if WORKSPACE_DIR.is_dir():
        for child in sorted(WORKSPACE_DIR.iterdir()):
            if child.is_dir() and (child / "config.json").is_file():
                names.append(child.name)
    return names


def load_config(env: str, auto_bootstrap: bool = False) -> tuple[Dict[str, Any], str]:
    """Load and validate one complete environment configuration."""
    defaults = load_defaults()
    env = str(env or ROOT_ENV)
    path = config_path_for(env)
    if path.is_file():
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise RuntimeError("Environment config must be a JSON object: %s" % path)
        config = dict(defaults)
        config.update(loaded)
    elif env == ROOT_ENV:
        # The root default always falls back to the template in memory; the only
        # way to persist a root config.json is 'environment_admin.sh init'.
        config = dict(defaults)
    elif auto_bootstrap:
        config = _bootstrap_env(env, defaults)
    else:
        available = ", ".join(available_environments()) or "(none)"
        raise RuntimeError(
             "No config for environment %r. Create it with "
             "'environment_admin.sh init %s', or run backend.py to auto-create it. "
             "Available: %s" % (env, env, available))
    config["works"] = normalize_works(config.get("works"))
    return config, env


def _root_source_dir() -> str:
    """The root default's source_dir, inherited by a newly created named env."""
    root_path = ROOT_CONFIG_PATH
    if root_path.is_file():
        with open(root_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and loaded.get("source_dir"):
            return str(loaded["source_dir"])
    return str(load_defaults().get("source_dir"))


def _bootstrap_env(env: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Create a missing named env's config.json from the template.

    The source_dir is inherited from the root default so a freshly created env
    points at the usual screenshots folder until it is configured otherwise."""
    config = dict(defaults)
    config["source_dir"] = _root_source_dir()
    path = config_path_for(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return config


def normalize_works(works):
    """Validate and normalize configured per-source work definitions."""
    if works is None:
        works = load_defaults().get("works")
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


def resolve_environment(env=DEFAULT_ENV, auto_bootstrap: bool = True):
    """Return config plus all environment-owned artifact paths.

    With ``auto_bootstrap`` a missing *named* env is created from the template
    (its source_dir inheriting the root default); the root env, when absent, is
    served from the template in memory without writing a file."""
    config, env = load_config(env, auto_bootstrap=auto_bootstrap)
    out_dir = env_dir(env)
    config["env_dir"] = out_dir
    config["tracker_path"] = out_dir / "_tracker.json"
    config["annotations_path"] = out_dir / "_annotations.jsonl"
    config["data_dir"] = out_dir
    config["db_path"] = out_dir / "wiki.db"
    config["exports_dir"] = out_dir
    config["thumbnails_dir"] = out_dir / "thumbnails"
    config["source_dir"] = os.path.expanduser(str(config["source_dir"]))
    config["temp_dir"] = os.path.expanduser(str(config["temp_dir"]))
    return env, config


CURRENT_ENV, CURRENT_CONFIG = resolve_environment()
