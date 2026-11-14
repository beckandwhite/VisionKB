"""End-to-end integration test for the picture pipeline (issue #14).

Runs one synthetic image through the real pipeline entry point (`backend.run`)
with the workspace redirected to a temp dir, then asserts the two artifacts the
run must produce: a `work1_generic.jsonl` record for the fixture and a generated
thumbnail. The test is *skipped* (never failed) when Ollama is unreachable, so it
is a no-op on machines or CI jobs without Ollama -- e.g. the plain `unittest`
smoke job.
"""

import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

# pytest is only installed in the integration job; the plain `unittest` smoke job
# does not have it, so guard the import to keep this module importable there too.
try:
    import pytest
except ModuleNotFoundError:
    pytest = None

# Repo root (the parent of this tests/ dir) so `backend` / `config_loader` import
# from whatever directory pytest happens to run from.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def ollama_available():
    """Return True if an Ollama HTTP endpoint answers on the configured base URL."""
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        urllib.request.urlopen(base + "/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _needs_ollama(func):
    """Skip `func` when Ollama is unreachable.

    Under pytest this registers a real skip mark; under `unittest` (which has no
    module-level skip here) it is a no-op because `unittest` never collects
    standalone functions, so the module still imports cleanly.
    """
    if pytest is not None:
        return pytest.mark.skipif(not ollama_available(), reason="Ollama not reachable")(func)
    return func


@_needs_ollama
def test_pipeline_produces_work1_record_and_thumbnail(tmp_path):
    """One image through backend.run must yield a work1 record and a thumbnail."""
    import backend
    import config_loader

    sources = tmp_path / "images"
    sources.mkdir()
    workspace = tmp_path / "workspace"
    # Copy the committed synthetic fixture into a throwaway source dir.
    shutil.copy(REPO_ROOT / "tests" / "fixtures" / "fixture.jpg", sources / "fixture.jpg")

    # Redirect the pipeline's workspace into tmp so the run never touches ./.workspace.
    old_workspace, old_root = config_loader.WORKSPACE_DIR, config_loader.ROOT_CONFIG_PATH
    config_loader.WORKSPACE_DIR = workspace
    config_loader.ROOT_CONFIG_PATH = workspace / "config.json"
    try:
        args = argparse.Namespace(
            env="itest",
            screenshot_dir=str(sources),
            count=None,
            until=None,
            wait=True,
        )
        backend.run(args)
    finally:
        config_loader.WORKSPACE_DIR = old_workspace
        config_loader.ROOT_CONFIG_PATH = old_root

    env_dir = workspace / "itest"
    result_file = env_dir / "work1_generic.jsonl"
    assert result_file.is_file(), "work1_generic.jsonl was not written"
    records = [
        json.loads(line)
        for line in result_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(rec.get("source_key", "").endswith("fixture.jpg") for rec in records), (
        "no work1 record for the fixture"
    )

    thumb = env_dir / "thumbnails" / "fixture.jpg"
    assert thumb.is_file(), "thumbnail was not generated"
    assert thumb.stat().st_size > 0, "thumbnail is empty"
