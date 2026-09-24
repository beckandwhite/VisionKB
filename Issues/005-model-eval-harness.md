# 005 · Vision-model eval harness (LLM-as-judge)

**Status:** Ready
**Priority:** Medium
**Component:** new `eval/` module · reuses `work_common.py`
**Labels:** `qa` `eval` `vision` `track-c`
**Depends on:** none to run *locally*; [004](004-private-runner.md) to run *in CI*
**Plan:** [Plans/QualityAssurance-CI.md](../Plans/QualityAssurance-CI.md)

---

## Problem

We swap vision models by editing `config["vision_model"]`, but we have no way to
answer *"how much better/worse is model X than model Y for this pipeline?"* We
want a repeatable harness that runs several candidate models over a fixed image
set, scores their outputs with an **LLM-as-judge** (Decision 2), and produces a
human-readable comparison report + machine-readable results.

Runs **locally first** (proves value with zero infra), then unchanged on the
self-hosted runner from [004](004-private-runner.md).

**Locked at grooming:**
- **Judge = both, config-selectable** (Decision 6): a `local` (Ollama) judge and
  an `api` (frontier) judge behind one interface; `eval_config.json` selects
  which runs. Neither ever receives images.
- **Privacy** (Decision 5): the eval image set is **sanitized/synthetic** and
  lives **outside git** (gitignored `eval/dataset/`). Personal iCloud screenshots
  are never committed. The `api` judge receives **derived text only** (candidate
  outputs + rubric), never images.

---

## Acceptance criteria

**Runner & config**
- [ ] `eval/compare_models.py` runs standalone (stdlib + reuse of
      `work_common.ollama_post_json` / `vision_request`); no heavy new runtime deps.
- [ ] `eval/eval_config.json` drives everything (shape consistent with
      `config.template.json`): `ollama_base`, `candidate_models` (list of
      `vision_model` ids), `works` (subset of `work1`/`work2`/`work3`),
      `dataset_dir`, and a `judge` block (below).
- [ ] The `judge` config block selects implementation:
      `{"type": "ollama", "model": "<id>"}` **or**
      `{"type": "api", "provider": "anthropic", "model": "<id>"}`.
      API credentials come from env (e.g. `ANTHROPIC_API_KEY`), **never** committed.

**Run behaviour**
- [ ] For each `(image × work-prompt × candidate model)` it records raw output,
      wall-clock **latency**, and any error — resiliently (one model/image failure
      does not abort the run; failures appear in the report).
- [ ] Candidate work prompts are **imported from `work1`/`work2`/`work3`**, not
      copied, so the eval measures real pipeline behaviour and stays in sync.
- [ ] The judge scores each candidate output against a **per-work rubric**
      (`eval/rubrics/work{1,2,3}.md`) and returns structured `{score: 1–5,
      rationale}` parsed via `work_common.parse_json_response`.
- [ ] **Images are never sent to the judge** — only candidate text output + the
      rubric + operator-supplied expected-answer notes for that image (if any).

**Output**
- [ ] Aggregation produces per-model mean score per work, pairwise score
      **deltas**, latency percentiles, and an overall ranking.
- [ ] **HTML report** (`eval/report.html`, style consistent with the existing
      `tag_review*.html` artifacts): per-image side-by-side of each model's output
      + judge score/rationale, plus summary tables. Also emits `eval/results.jsonl`
      (one record per image×model×work) for later analysis.
- [ ] `eval/dataset/`, `eval/report.html`, `eval/results.jsonl` are **gitignored**.
- [ ] A `--sample N` / `--dry-run` mode runs against 1–2 images for fast iteration
      and a future CI smoke variant.
- [ ] `eval/README.md` documents pointing at an Ollama endpoint, adding candidate
      models, choosing a judge, and reading the report.

---

## Proposed implementation (sketch)

```
eval/
  eval_config.json        # candidate models, judge block, works, dataset_dir
  rubrics/
    work1.md              # what "good" means for the generic vision summary
    work2.md              # OCR fidelity rubric
    work3.md              # classification-correctness rubric
  judges.py               # judge interface + OllamaJudge + ApiJudge
  compare_models.py       # orchestrator
  README.md
  dataset/                # sanitized/synthetic images (gitignored)
  report.html             # generated (gitignored)
  results.jsonl           # generated (gitignored)
```

**Judge interface** (config-selectable, both implementations):

```python
# eval/judges.py
class Judge:
    def score(self, work: str, candidate_output: str, expected_notes: str) -> dict:
        """Return {'score': 1..5, 'rationale': str}. Never receives the image."""

class OllamaJudge(Judge):   # reuses work_common.ollama_post_json against judge model
    ...
class ApiJudge(Judge):      # frontier API; text-only; key from env, never committed
    ...

def build_judge(cfg: dict) -> Judge:
    return {"ollama": OllamaJudge, "api": ApiJudge}[cfg["judge"]["type"]](cfg)
```

**Orchestrator flow:**

```python
cfg = load("eval/eval_config.json")
judge = build_judge(cfg)
for model in cfg["candidate_models"]:
    for image in images(cfg["dataset_dir"]):
        for work in cfg["works"]:
            t0 = time.time()
            try:
                out = work_common.vision_request(image, PROMPTS[work],
                        {**base, "vision_model": model})
            except Exception as e:
                record_error(...); continue
            latency = time.time() - t0
            verdict = judge.score(work, out, expected_notes(image, work))  # {score, rationale}
            append(results, model, image, work, out, latency, verdict)
aggregate_and_render(results)   # eval/results.jsonl + eval/report.html
```

---

## Out of scope

- Golden-set exact-match metrics (Decision 2 chose LLM-as-judge). Could later add
  as a complementary signal for `work2`/`work3`.
- CI gating on quality regression — start **informational** (report only); gating
  is a follow-up once scores are trusted (plan, resolved-Q6).
- Embedding-model (`nomic-embed-text`) evaluation — separate concern.
- Judges receiving images (multimodal judging) — explicitly excluded for privacy.

---

## References

- `work_common.py` — `vision_request`, `ollama_post_json`, `parse_json_response`
- `work1.py` / `work2.py` / `work3.py` — prompts to import
- `tag_review.html`, `tag_review_entities.html` — report style precedent
- `config.template.json` — config-shape precedent
- `recommendedHW.md` — hardware/model context for candidate selection
