# Vision-model eval harness

Compares vision models over a fixed image set, scores each output with an
LLM-as-judge against per-work rubrics, and produces an HTML report +
machine-readable JSONL. Built in [#32](https://github.com/beckandwhite/VisionKB/issues/32).

## What's here

| File | Role |
|---|---|
| `eval_config.json` | Candidate models, judge, dataset path, works. |
| `compare_models.py` | Orchestrator — runs every (image × work × model) cell. |
| `judges.py` | LLM-as-judge backends (Ollama, Anthropic API, dry-run stub). |
| `rubrics/work1.md` | Scoring rubric — generic vision description. |
| `rubrics/work2.md` | Scoring rubric — OCR fidelity. |
| `rubrics/work3.md` | Scoring rubric — classification. |
| `dataset/manifest.txt` | 300 absolute paths to sampled screenshots. **Gitignored.** |
| `report.html`, `results.jsonl` | Generated outputs. **Gitignored.** |

## Quick start

### 1. Finish config (issue [#64](https://github.com/beckandwhite/VisionKB/issues/64))

`eval_config.json` still has `REPLACE-…` placeholders. Fill them in once:

```json
"ollama_base":           "http://D72nq7w67l.local:11434",
"candidate_models":      ["muse-glimmer:30b-mlx", "gemma4:12b-mlx"],
"judge.ollama.model":    "qwen3.6:35b-mlx"
```

### 2. Smoke-test (no network needed)

```bash
python3 eval/compare_models.py --dry-run
```

Expected: `300 new images · 3 works · 2 models · 1800 new cells · 1800 cells total · DRY RUN`
(writes to `results.dryrun.jsonl` / `report.dryrun.html`, not the real outputs)

### 3. Run a small sample first

```bash
python3 eval/compare_models.py --sample 5
```

Runs the **next 5** not-yet-recorded images × 3 works × 2 models = 15 new cells
(appended to `results.jsonl`), then refreshes `eval/report.html` over **all**
cells recorded so far. Takes a few minutes. Check `eval/report.html` before
committing to the full set.

Re-running `--sample 5` processes the *next* 5 (it does not redo the first 5) —
the harness resumes from `results.jsonl` by default.

### 4. Run the rest

```bash
python3 eval/compare_models.py
```

Resumes: processes every remaining not-yet-recorded image to completion
(starting from a blank run that's 300 × 3 × 2 = 1800 cells). Wall-clock time
depends on model speed — expect 30–90 min for the 30B model, less for 12B.

### Flags

| Flag | Meaning |
|---|---|
| `--sample N` | Process the next N not-yet-recorded images (resume), or the first N with `--fresh`. |
| `--offset N` | Skip the first N images of the list before selecting (pair with `--fresh --sample`). |
| `--fresh` | Ignore `results.jsonl` and start over (the old overwrite behavior). |
| `--dry-run` | Smoke test, no network. Writes to `results.dryrun.jsonl` / `report.dryrun.html` so it never clobbers real data. |

## Dataset

Images are referenced by absolute path from `eval/dataset/manifest.txt`
(one path per line, `#` = comment). The manifest is gitignored and
machine-local. It currently holds 300 paths randomly sampled (seed=42)
from `~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`.

To resample or expand:

```python
import os, random

SCREENSHOTS = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/")
SUPPORTED = {".png", ".jpg", ".jpeg", ".heic"}
all_images = sorted(
    p for p in os.listdir(SCREENSHOTS) if os.path.splitext(p)[1].lower() in SUPPORTED
)
random.seed(42)
selected = random.sample(all_images, 300)  # change N here
with open("eval/dataset/manifest.txt", "w") as fh:
    for name in sorted(selected):
        fh.write(os.path.join(SCREENSHOTS, name) + "\n")
```

To add ground-truth notes for the judge, create
`eval/dataset/expected_notes.json` — a `{"filename.png": "note"}` map.
Missing entries are fine; the judge gets "(none provided)" for those images.

## Judge backends

| `judge.type` | Where it runs | Privacy |
|---|---|---|
| `"ollama"` | Local Ollama host | Nothing leaves the LAN |
| `"api"` | Anthropic API (text-only) | Candidate **text** output sent off-box; images never sent |

For `"api"`, set `judge.api.api_key_env` to the name of the env var holding
your API key (e.g. `ANTHROPIC_API_KEY`). The key is never logged or written
to disk.

## Report

`eval/report.html` contains:

- **Ranking table** — overall mean judge score per model
- **Model × work table** — mean score per (model, work) cell
- **Pairwise deltas** — score difference between every model pair, per work
- **Latency percentiles** — p50 / p90 / p95 wall-clock seconds per model
- **Per-image side-by-side** — raw output + judge score + rationale for every cell

Pending issues enrich the report further:
[#65](https://github.com/beckandwhite/VisionKB/issues/65) tokens/sec ·
[#66](https://github.com/beckandwhite/VisionKB/issues/66) error rate ·
[#67](https://github.com/beckandwhite/VisionKB/issues/67) 0–10 normalized scores + efficiency metric

## Privacy

The judge is **never** given images — only the candidate model's text output,
the rubric, and optional `expected_notes`. The manifest paths are local and
gitignored. Never commit personal screenshots.
