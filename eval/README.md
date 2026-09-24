# Vision-model eval harness

Scaffold for [Issue 005](../Issues/005-model-eval-harness.md) — compare vision
models over a fixed image set, scored by an LLM-as-judge, and produce an HTML
report. **These are stubs**: the config and rubrics are here; the orchestrator
(`compare_models.py`) and judges (`judges.py`) are still to be implemented per
the issue.

## What's here

| File | Role |
|---|---|
| `eval_config.json` | Candidate models, judge selection, works, paths. **Fill in the `REPLACE-…` values.** |
| `rubrics/work1.md` | Scoring rubric for the generic-description work. |
| `rubrics/work2.md` | Scoring rubric for OCR fidelity. |
| `rubrics/work3.md` | Scoring rubric for classification. |
| `dataset/` | Sanitized/synthetic eval images. **Gitignored — never commit personal screenshots.** |
| `report.html`, `results.jsonl` | Generated outputs (gitignored). |

## Before running (once the harness exists)

1. Put sanitized/synthetic images in `eval/dataset/` (see privacy note in the issue).
2. Edit `eval_config.json`:
   - `ollama_base` → your endpoint.
   - `candidate_models` → the models to compare (add ≥2 for a real comparison).
   - `judge.type` → `"ollama"` (local, nothing leaves the LAN) or `"api"`
     (frontier, **text-only** off-box). Fill the matching sub-block; for `api`,
     set the API key in the env var named by `judge.api.api_key_env`.
3. Run the (future) `python3 eval/compare_models.py` — use `--sample N` /
   `--dry-run` for a quick smoke over 1–2 images.

## Privacy

The judge is **never** given images — only candidate text output + the rubric +
optional operator `expected_notes`. Keep the dataset sanitized/synthetic and out
of git.
