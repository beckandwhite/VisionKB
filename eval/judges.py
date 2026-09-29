"""LLM-as-judge scoring for the vision-model eval harness (issue #32).

The judge is given DERIVED TEXT ONLY -- the candidate model's output text, the
relevant rubric, and optional operator ``expected_notes``. It is NEVER given an
image; that invariant is enforced by construction here (there is no code path
that accepts image bytes).

Two real backends plus a dry-run stub:

* ``OllamaJudge`` -- local model, reusing ``work_common.ollama_post_json``.
* ``ApiJudge``    -- text-only frontier model over stdlib ``urllib`` (no third
  party SDK). The API key is read from the env var named in config and is
  never logged or written to disk.
* ``DryRunJudge`` -- returns a fixed verdict without any network call, so the
  aggregation + report paths can be exercised in CI.

Stdlib-only. Reuses ``work_common`` (imported, not copied).
"""

import json
import os
import sys
import urllib.error
import urllib.request

# work_common lives at the repo root; make it importable regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import work_common  # noqa: E402  (path bootstrap must run first)

_RUBRICS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rubrics")

JUDGE_INSTRUCTIONS = (
    "You are a strict evaluator for a vision-model eval harness.\n"
    "You are given TEXT ONLY -- the rubric, the candidate model's output, and "
    "optional operator notes. You are NEVER shown the image; judge only from "
    "the text you are given.\n"
    "Apply the rubric's 1-5 anchors and respond with STRICT JSON and nothing "
    'else: {"score": <integer 1-5>, "rationale": "<one or two sentences>"}.'
)


def build_prompt(rubric_text, output_text, expected_notes):
    """Assemble the text-only judge prompt from the rubric + candidate output."""
    notes = (expected_notes or "").strip() or "(none provided)"
    return (
        f"{JUDGE_INSTRUCTIONS}\n\n"
        f"----- RUBRIC -----\n{rubric_text}\n\n"
        f"----- CANDIDATE OUTPUT (text only) -----\n{output_text}\n\n"
        f"----- OPERATOR EXPECTED NOTES -----\n{notes}\n\n"
        "Return the strict JSON verdict now."
    )


def _coerce(parsed):
    """Clamp/normalise a parsed judge response to {'score':1..5,'rationale':str}."""
    if not isinstance(parsed, dict):
        raise ValueError("judge response was not a JSON object")
    try:
        score = int(round(float(parsed.get("score"))))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"judge response had no usable score: {parsed!r}") from exc
    score = max(1, min(5, score))
    rationale = str(parsed.get("rationale", "")).strip()
    return {"score": score, "rationale": rationale}


class Judge:
    """Base judge: loads the rubric and drives the strict-JSON scoring flow."""

    def __init__(self, rubrics_dir=None):
        self.rubrics_dir = rubrics_dir or _RUBRICS_DIR

    def _rubric(self, work):
        path = os.path.join(self.rubrics_dir, f"{work}.md")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def _complete(self, prompt):
        raise NotImplementedError

    def score(self, work, output, expected_notes=None):
        """Score one candidate ``output`` for ``work`` -> {'score','rationale'}."""
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False)
        prompt = build_prompt(self._rubric(work), output, expected_notes)
        raw = self._complete(prompt)
        return _coerce(work_common.parse_json_response(raw))


class OllamaJudge(Judge):
    """Judge backed by a local Ollama model (reuses work_common's JSON POST)."""

    def __init__(self, base_url, model, rubrics_dir=None):
        super().__init__(rubrics_dir)
        self.base_url = base_url
        self.model = model

    def _complete(self, prompt):
        response = work_common.ollama_post_json(
            self.base_url,
            "/api/generate",
            {"model": self.model, "prompt": prompt, "stream": False},
        )
        text = response.get("response", "")
        if not text:
            raise RuntimeError("judge (ollama) returned no response")
        return text.strip()


class ApiJudge(Judge):
    """Text-only frontier judge over stdlib urllib. Key from env, never logged."""

    def __init__(self, provider, model, api_key_env, rubrics_dir=None):
        super().__init__(rubrics_dir)
        self.provider = provider
        self.model = model
        self.api_key_env = api_key_env
        self.api_key = os.environ.get(api_key_env)
        if not self.api_key:
            raise RuntimeError(f"API judge needs the {api_key_env} environment variable to be set")

    def _complete(self, prompt):
        if self.provider == "anthropic":
            return self._anthropic(prompt)
        raise RuntimeError(f"unsupported API judge provider: {self.provider!r}")

    def _anthropic(self, prompt):
        payload = {
            "model": self.model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            # Text-only HTTPS call to the operator-configured judge API; the URL
            # is built from config, not untrusted input (see DECISIONS.md #30 B310).
            with urllib.request.urlopen(request, timeout=180) as response:  # nosec B310
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            # Never include self.api_key in the message.
            raise RuntimeError(f"API judge request failed: {exc}") from exc
        parts = data.get("content", [])
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not text:
            raise RuntimeError("API judge returned no text content")
        return text


class DryRunJudge(Judge):
    """Offline stub: fixed verdict, no network, for --dry-run / CI smoke."""

    def _complete(self, prompt):
        return json.dumps({"score": 3, "rationale": "dry-run stub judge: no model was queried."})


def build_judge(cfg, dry_run=False, rubrics_dir=None):
    """Factory: pick the judge backend from ``cfg['judge']['type']``.

    When ``dry_run`` is set, a :class:`DryRunJudge` is returned regardless of the
    configured type so the harness needs no live endpoint.
    """
    if dry_run:
        return DryRunJudge(rubrics_dir)
    judge_cfg = cfg["judge"]
    judge_type = judge_cfg["type"]
    if judge_type == "ollama":
        return OllamaJudge(cfg["ollama_base"], judge_cfg["ollama"]["model"], rubrics_dir)
    if judge_type == "api":
        api = judge_cfg["api"]
        return ApiJudge(api["provider"], api["model"], api["api_key_env"], rubrics_dir)
    raise ValueError(f"unknown judge type: {judge_type!r}")
