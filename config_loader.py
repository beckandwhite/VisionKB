import json
import os
import sys
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


def load_config(env: str, auto_bootstrap: bool = False,
                source_dir: str = None) -> tuple[Dict[str, Any], str]:
    """Load and validate one complete environment configuration.

    The root default is materialised from the template into a real
    ``.workspace/config.json`` the first time it is needed (only when
    ``auto_bootstrap`` is set); after that the persisted copy is the source of
    truth. A *named* env is created from the template with ``auto_bootstrap``.
    Without ``auto_bootstrap`` a missing config raises an actionable error."""
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
    elif env == ROOT_ENV and auto_bootstrap:
        config = bootstrap_root_config(source_dir=source_dir)
    elif auto_bootstrap:
        config = _bootstrap_env(env, defaults)
    else:
        if env == ROOT_ENV:
            raise RuntimeError(
                "Default environment config %s is missing. Run "
                "'python3 backend.py' to initialise it first." % ROOT_CONFIG_PATH)
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


def _write_config(path, config: Dict[str, Any]) -> None:
    """Atomically write a config object to disk (temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def bootstrap_root_config(source_dir: str = None,
                          interactive=None) -> Dict[str, Any]:
    """Materialise the default (.workspace/) config from the template.

    The first time the default env is used there is no ``.workspace/config.json``
    yet, so this copies the template into one (the template stays the canonical
    source of truth). It is idempotent: an existing config.json is left untouched.
    The ``source_dir`` is written from the explicit argument, else — on an
    interactive terminal — prompted for, else left at the template default."""
    if ROOT_CONFIG_PATH.is_file():
        with open(ROOT_CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    config = load_defaults()
    template_default = str(config["source_dir"])
    chosen = (str(source_dir).strip() if source_dir else "")
    if not chosen and interactive is not False and sys.stdin.isatty():
        answer = input(
            "Source folder for the default environment "
            "(blank = keep %s): " % template_default).strip()
        chosen = answer
    config["source_dir"] = chosen or template_default
    _write_config(ROOT_CONFIG_PATH, config)
    return config


def _bootstrap_env(env: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Create a missing named env's config.json from the template.

    The source_dir is inherited from the root default so a freshly created env
    points at the usual screenshots folder until it is configured otherwise."""
    config = dict(defaults)
    config["source_dir"] = _root_source_dir()
    _write_config(config_path_for(env), config)
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


def resolve_environment(env=DEFAULT_ENV, auto_bootstrap: bool = True,
                       source_dir: str = None):
    """Return config plus all environment-owned artifact paths.

    With ``auto_bootstrap`` a missing env is created from the template: the root
    default gets its own ``.workspace/config.json`` (writing the ``source_dir``
    here), and a *named* env inherits the root default's ``source_dir``. Without
    ``auto_bootstrap`` a missing config raises an actionable error."""
    config, env = load_config(env, auto_bootstrap=auto_bootstrap,
                              source_dir=source_dir)
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
