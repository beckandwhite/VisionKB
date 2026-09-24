# 002 · Baseline CI pipeline (lint, format, smoke)

**Status:** Ready
**Priority:** High
**Component:** `.github/workflows/` (new) · repo-wide
**Labels:** `ci` `tooling` `track-a`
**Depends on:** none
**Plan:** [Plans/QualityAssurance-CI.md](../Plans/QualityAssurance-CI.md)

---

## Problem

There is no CI. Every check (does it import, does it lint, does it run) is manual
on one developer's Mac. We want a fast, hardware-free pipeline on GitHub-hosted
runners that gates every PR on basic correctness and style, and establishes the
workflow skeleton the security and QA tracks build on.

The core is **stdlib-only Python** (see plan), so this workitem is about *our*
code hygiene, not third-party dependencies. There are no tests yet; this creates
the harness and a minimal smoke check, leaving a real test suite as follow-up.

---

## Acceptance criteria

- [ ] `.github/workflows/ci.yml` runs on `pull_request` and `push` to `main`.
- [ ] Job uses `ubuntu-latest`, Python `3.11` (matches the `__pycache__`
      cpython-311 artifacts), and completes without needing Ollama, `sips`, or
      any image/network access.
- [ ] **Lint + format**: `ruff check .` and `ruff format --check .` run and pass
      (or the initial run fixes/whitelists existing findings so the baseline is
      green). Ruff config lives in `pyproject.toml` (new) or `ruff.toml`.
- [ ] **Import smoke test**: every core module (`backend.py`, `frontend.py`,
      `config_loader.py`, `tracker.py`, `work_common.py`, `work1`–`work7`,
      `ner.py`) imports without error under a clean Python 3.11 (no `.venv`).
      A tiny `tests/test_imports.py` using stdlib `unittest` is acceptable — no
      pytest dependency required, but pytest is fine if added to a dev-deps file.
- [ ] **Config smoke test**: `config.template.json` parses as valid JSON and
      contains the required keys (`ollama_base`, `vision_model`, `embed_model`,
      `TAG_LIST`). Assert via the smoke test.
- [ ] All third-party GitHub Actions are pinned to a **full commit SHA**, not a
      floating tag (prep for [003](003-security-supply-chain.md) hardening).
- [ ] Workflow declares least-privilege `permissions:` (`contents: read`) at the
      top level.
- [ ] A status badge is added to `README.md`.
- [ ] CI green on a throwaway PR.

---

## Proposed implementation

**`pyproject.toml`** (new — tooling config only; does not make the runtime
non-stdlib):

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = [".venv", "__pycache__", ".workspace", "Plans", "Issues"]

[tool.ruff.lint]
# Start conservative so the baseline goes green; tighten later.
select = ["E", "F", "W", "I"]   # pycodestyle, pyflakes, isort
```

**`.github/workflows/ci.yml`** (sketch — pin SHAs at implementation time):

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  lint-and-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>            # v4
      - uses: actions/setup-python@<sha>        # v5
        with:
          python-version: "3.11"
      - name: Install ruff
        run: pipx install ruff
      - name: Ruff lint
        run: ruff check .
      - name: Ruff format check
        run: ruff format --check .
      - name: Smoke tests
        run: python -m unittest discover -s tests -p 'test_*.py' -v
```

**`tests/test_imports.py`** (sketch):

```python
import importlib, json, unittest

CORE = ["backend", "frontend", "config_loader", "tracker", "work_common",
        "work1", "work2", "work3", "work4", "work5", "work6", "work7", "ner"]

class TestImports(unittest.TestCase):
    def test_core_modules_import(self):
        for m in CORE:
            with self.subTest(module=m):
                importlib.import_module(m)

class TestConfigTemplate(unittest.TestCase):
    def test_template_valid(self):
        with open("config.template.json") as fh:
            cfg = json.load(fh)
        for key in ("ollama_base", "vision_model", "embed_model", "TAG_LIST"):
            self.assertIn(key, cfg)

if __name__ == "__main__":
    unittest.main()
```

> If any core module executes network/Ollama/`sips` calls at import time, refactor
> the offending top-level code behind `if __name__ == "__main__":` or a `main()`
> guard as part of this workitem — import must be side-effect-free.

---

## Out of scope

- Security scanners, CodeQL, Dependabot → [003](003-security-supply-chain.md).
- Self-hosted runners / Ollama in CI → [004](004-private-runner.md).
- A real behavioural test suite (beyond import/config smoke) — follow-up.

---

## References

- `work_common.py`, `backend.py`, `config.template.json`
- Ruff: https://docs.astral.sh/ruff/
- Pinning actions to SHAs: https://docs.github.com/actions/security-guides/security-hardening-for-github-actions
