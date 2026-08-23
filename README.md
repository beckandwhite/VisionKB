# Screenshot Knowledgebase

Turn a folder of screenshots (or other pictures later) into a local, searchable, **LLM-written wiki**.
Raw per-image metadata is extracted with a local vision model, then synthesized
into clustered topic pages + a timeline.

Use cases in mind: 
1. Screenshots
2. random everyday Photos (taken by phone) without context (other than EXIF, GPS)

> Status: **experimental**. The extraction + ingestion pipeline runs; the
> clustering + LLM-wiki synthesis layers are still to be built — see
> [implementation.md](Plans/implementation.md).

## TLDR

```bash
# 1. Backend: classify the next N screenshots → build the KB + thumbnails
python3 backend.py -env PRD-iCloud-Screenshots --count 5

# 2. Frontend: view the results in a browser
python3 frontend.py -env PRD-iCloud-Screenshots --port 8000 --open   # http://127.0.0.1:8000
```

Point both at the same environment with `-env`. The frontend is read-only and
re-reads the environment's tracker, annotations, exports, and thumbnails on every
request, so the backend must have produced at least one record first
(`--count 0` = all remaining; see "Backend options" below).

---

## What it does, How it is working



Each image becomes one JSON record: `tags[]`, `OCR_text[]`, `entities[]`,
 `caption`, `quality_score (1-5)` (the vision model's confidence that the
 screenshot is crisp and readable — 5 = clearly readable, 1 = blurry/unusable),
 and a 768-dim embedding. The ingestion step
 builds an FTS5 full-text index, tag co-occurrence graph, and monthly histogram.

Every run **also** writes a 320px JPEG thumbnail per annotated image into
`.workspace/<env>/thumbnails/`, refreshed after each image so the WebUI shows
live previews. Pass `--no-thumbs` to skip it. See "Backend options" below.

The read-only **frontend** then serves those same artifacts to a browser:

```
                reconcile → classify → ingest → thumbnails → exports

screenshots ──► backend.py ──► .workspace/<env>/

(images)        (one image at a time, so the KB + UI stay live)
                          
                          └───► _annotations.jsonl + wiki.db +
                                wiki.ndjson  + tags_index.json  + thumbnails/


 .workspace/<env>/   ──►  frontend.py  ──►  browser (http://127.0.0.1:8000)

 (the exports +         (re-reads per      explore the extracted info:
  thumbnails)           request)           backlog → timeline → tags
```

---

## Prerequisites

On macOS, no `pip` install required — the scripts are **stdlib-only**.

| Requirement | Where | Needed for |
|---|---|---|
| **Python 3.9+** (3.9.6 verified) | `/usr/bin/python3` | everything |
| **sips** | `/usr/bin/sips` (preinstalled) | HEIC→JPEG, thumbnails — the only binary the code calls |
| **Ollama** + models below | homebrew | vision + embeddings |
### Ollama models (must be pulled)

```bash
ollama pull muse-glimmer:30b-mlx     # Lare vision model  (relative slow: ~90s/image on an M5 Pro Macbook Pro)
ollama pull nomic-embed-text         # embedding model
```

Start the server and confirm it is up:

```bash
ollama serve &                          # or: it may already be running
curl -s localhost:11434/api/tags | jq   # list models; expect the two above
```
---

## Layout

```
Repo/
 ├── tracker.py             # shared tracker module (registry + telemetry + KB stamps)
 ├── config_loader.py       # environment configuration and artifact paths
 ├── frontend.py            # read-only WebUI over the tracker + exports
 ├── backend.py            # single entry point: reconcile → classify → ingest → exports
 └── .workspace/<env>/      # config + isolated annotations/tracker/KB artifacts
```

Images themselves live in the configured source folder. Select an environment
with `-env` (default `PRD-iCloud-Screenshots`):

```bash
python3 backend.py -env QA
python3 backend.py -env PRD-iCloud-Screenshots
```

Images themselves commonly live in iCloud:
`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`

`backend.py` defaults to the active environment's `source_dir`; point it elsewhere with
`--screenshot-dir` (see "How to use" below).

The environment's `_tracker.json` is the **single source of truth for progress + telemetry**: it
holds the per-file registry, a `runs` summary, each file's analysis lifecycle
(`started_at` / `finished_at` / latency / `status` / `error`), and KB-layer
progress (`ingested_at` / `thumb_at`). The old standalone `telemetry.log` is gone.

---

## How to use

### 1. Run the pipeline (classification + knowledgebase)

```bash
# classify and ingest the next 5 unprocessed files in PRD-iCloud-Screenshots
python3 backend.py --count 5

# use the environment's configured limit (QA=100)
python3 backend.py -env QA

# scan a different folder with --screenshot-dir
python3 backend.py --screenshot-dir '~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/' --count 5

# classify all remaining unprocessed files (no limit)
python3 backend.py                    # configured limit; --count 0 = all remaining
```

`.workspace/<env>/_tracker.json` is a **self-maintaining registry**, not a bare index. Each run:

1. **Reconciles** the source folder into the tracker — every file is stored (keyed
   by absolute path) with its `filename` and `mtime_iso`; files new since the last
   run are **appended** automatically.
2. **Marks progress** with a `processed_at` timestamp the moment each file is done,
   so an interrupted or crashed run leaves an accurate ledger.
3. **Classifies the next N unprocessed files** (`--count N`, newest mtime first;
   `--count 0` = all remaining). `--count` is the per-run limit, not "the first N
   images ever".

- **Re-runs skip what's done.** A re-run with no new files prints *Nothing to do*
  and does no vision work. Add a file (or run with more of them present) and only the
  new/unprocessed ones are picked up.
- **Existing annotations are auto-seeded.** On the first run after this change,
  files already present in `_annotations.jsonl` are marked processed (`status:
  "backfilled"`) so they are not reclassified.
 - **Note on edited files:** a file already in the registry keeps its
   `finished_at` even if its mtime changes, so an in-place edit is **not**
  reprocessed by the classifier (keyed by path, not by content/mtime).
  The KB ingestion stage, though, detects an mtime change and re-ingests that row.
   Delete a registry entry (and its `_annotations.jsonl` line) to force a full
   re-process.
 - Checkpointed atomically (`tmp` + `os.replace`) every 25 files and at end of run.
 - `--screenshot-dir` overrides the active environment's configured source folder;
   generated outputs stay inside `.workspace/<env>/`, not in the scanned folder.
 - HEIC files are auto-converted via `sips`; oversized images are downscaled.
 - Latency + `status` (ok/fail/error) live in the tracker now (per-file
   `vision_latency_s` + `started_at` / `finished_at`); the old `telemetry.log`
   is retired.

Each image is classified and then immediately written to the environment's SQLite
database, FTS index, thumbnail set, and exported views. The exports are refreshed
after each image, so the read-only WebUI can be used during a long run.

To repair or rebuild the KB from annotations without calling the vision model:

```bash
python3 backend.py -env DEV --rebuild-kb --no-thumbs
python3 backend.py -env DEV --rebuild-kb --force
```

Produces `.workspace/<env>/wiki.db`, `.workspace/<env>/wiki.ndjson`,
and `.workspace/<env>/tags_index.json`; thumbnails are stored in
`.workspace/<env>/thumbnails/`.

`backend.py` is incremental: completed files are skipped on later runs. A
second writer for the same environment exits cleanly while another run holds
the environment lock; use `--wait` when waiting is preferred.

For a bounded nightly run, stop before starting another image at the next local
06:00 deadline:

```bash
python3 backend.py -env PRD-iCloud-Screenshots --until 06:00
```

For cron, use `run.sh` with an explicit environment. Example copy-paste entry:

```cron
0 0 * * * /Users/t/git/VisionKB/run.sh -env PRD-iCloud-Screenshots --until 06:00 >> /Users/t/git/VisionKB/pipeline.log 2>&1
```

Cron does not run reliably while the Mac sleeps, and Ollama plus the configured
source folder must be available. At roughly 90 seconds per image, 2,000 images
requires about 50 hours of model time before failures or duplicates.

### Backend options

`backend.py` is the single entry point that consolidated the former multi-stage
scripts. It interleaves `reconcile → classify → ingest → thumbnails → exports`,
one image at a time, so the KB (and the WebUI) stay live during a long run.
All options are passed on its command line:

| Flag | Meaning |
|---|---|
| `-env ENV` | Choose the environment: `DEV`, `QA`, `PRD-iCloud-Screenshots` (default), `PRD-OneDrive-Pictures`. All artifacts are isolated under `.workspace/<env>/`. |
| `--count N` | Process up to **N** unprocessed files this run (newest `mtime` first). Omit it to use the environment's `processed_limit` (DEV = 5, PRD = 0); `0` = all remaining. |
| `--screenshot-dir PATH` | Scan a folder other than the environment's configured `source_dir`. Generated outputs still land in `.workspace/<env>/`, not the scanned folder. |
| `--no-thumbs` | Skip 320px thumbnail generation (used by both the normal run and `--rebuild-kb`). |
| `--rebuild-kb` | Rebuild `wiki.db` + the exports from an existing `_annotations.jsonl` **without calling the vision model**. Incremental: a record is re-ingested only when its source `mtime` changed. |
| `--force` | With `--rebuild-kb`: **wipe every existing DB row** and re-ingest from scratch, ignoring the `mtime` change-detection. |
| `--until HH:MM` | Stop before starting the next image once the local wall-clock deadline passes (for a bounded nightly/cron run). |
| `--wait` | Wait for the environment lock instead of exiting cleanly when another run holds it. |

- **Incremental.** Completed files are skipped on later runs; a re-run with no
  new files prints *Nothing to do* and does no vision work.
- **One writer per environment.** A second `backend.py` for the same env exits
  cleanly while another run holds the lock; use `--wait` to queue instead.
- **Companion scripts.** `run.sh` is the thin cron wrapper (`exec backend.py "$@"`,
  so it takes the same flags). `reset.sh` clears only the cheap, regenerable KB
  layer — `wiki.db`, `wiki.ndjson`, `tags_index.json`, `thumbnails/` — and is
  rebuilt with `python3 backend.py -env ENV --rebuild-kb`; raw
  `_annotations.jsonl` is always kept.

### 3. Query (FTS5 full-text)

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect(".workspace/PRD-iCloud-Screenshots/wiki.db")
for cap, in c.execute("SELECT caption FROM screenshots_fts "
                      "WHERE screenshots_fts MATCH 'terminal OR coding' LIMIT 10"):
    print(cap[:100])
PY
```

 (Embedding/semantic search and the clustered **LLM-wiki** layer are planned —
 see [implementation.md](Plans/implementation.md) Stage 5.)

 ### 4. WebUI (timeline + backlog dashboard)

 A dependency-free single-page viewer over the pipeline artifacts (stdlib
 `http.server` + vanilla JS; no build). Read-only — it never writes or touches
 the pipeline scripts; every source file is re-read per request, so a live
`backend.py` run shows up without a restart.

 ```bash
python3 frontend.py -env PRD-iCloud-Screenshots --port 8000 --open               # http://127.0.0.1:8000
python3 frontend.py -env PRD-iCloud-Screenshots --port 8000 --open
# The UI reads the selected environment's tracker, annotations, exports, and thumbnails.
 ```

The WebUI defaults to `PRD-iCloud-Screenshots`. Use the same `-env` value as the pipeline so the
UI and processor point at the same isolated workspace, for example:

```bash
python3 backend.py -env PRD-iCloud-Screenshots --count 5
python3 frontend.py -env PRD-iCloud-Screenshots --port 8000 --open
```

 Three sections, top → bottom:
 - **Backlog** — funnel of pipeline stages as % of the tracker total
   (`_tracker.json` `total_files` ≈ 2027): *Scanned → Vision attempts →
   Vision ok → Annotated → Wiki-ingested*, plus a time-equivalent backlog
   (`avg latency × remaining` → ETA + projected finish), status chips
   (`ok / fail / error / pending`, plus `ingested` + `thumbnails` from the builder), and a per-run latency sparkline.
 - **Timeline** — rows based on tracker `files`, enriched from `_annotations.jsonl` and
   `wiki.ndjson` (filename key), newest first; filters by modification-time range,
   tag / status / free-text search; click a row to expand available OCR / entities /
   tags and an "open original" link. Pending tracker files are included with empty
   annotation fields.
   The `/api/timeline` endpoint accepts inclusive `mtime_from` and `mtime_to` epoch
   seconds and returns `mtime_min_epoch`, `mtime_max_epoch`, and 48 `mtime_buckets`
   derived from valid tracker `mtime_iso` values. Invalid timestamps are excluded
   from the heatmap domain.
 - **Tags** — `top_tags` bars + `edges` co-occurrence list from
  `tags_index.json`; clicking a tag filters the timeline.

 Thumbnails render live once `thumbnails/` is populated
(`python3 backend.py` without `--no-thumbs`); until then rows show a
 placeholder. See [WebUI-1.0-plan.md](Plans/WebUI-1.0-plan.md) for the design.

---

## Gotchas (read these first)

- **`backend.py` defaults to the active environment's source folder**
   (`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`); pass
   `--screenshot-dir` to scan elsewhere. Outputs (`_tracker.json`,
   `_annotations.jsonl`) are written next to the script, not into
   the scanned folder. The existing `_annotations.jsonl` is real history (5 records);
   its old flat-index `_tracker.json` is auto-migrated — the registry is rebuilt from
   the folder + those annotations on the first run.
- **Environment selection matters.** Use `-env` consistently for `backend.py`
  and `frontend.py`; all annotations, tracker state, database, exports, and
  thumbnails are isolated under `.workspace/<env>/`.
- **Cost: ~90 s/image with muse-glimmer:30b.** Ollama is effectively single-stream,
  so Python "concurrency" won't speed up vision. For the full ~2,000 images, see the
   dedup + cheaper-model strategy in see [implementation.md](Plans/implementation.md).
 - **python3 is 3.9**: no `match`, no runtime `X | Y` unions. Keep scripts stdlib-only.
 - **OCR is the vision model's, English-leaning:** non-English text won't be read
   well (there is no separate tesseract/ffmpeg in the pipeline — `sips` is the
   only external binary, used for HEIC→JPEG + thumbnails).

 ---

## Data files

| File | Purpose |
|---|---|
| `_annotations.jsonl` | one JSON record per image (append, never rewritten) |
| `_tracker.json` | per-file registry (filename + `mtime_iso` + `processed_at`) and run summary — the progress ledger |
| `tracker.py` | shared tracker module used by `backend.py` and `frontend.py` |
| `config_loader.py` | per-environment config + artifact paths (used by `backend.py` + `frontend.py`) |
| `telemetry.log` | (retired) telemetry now lives in `_tracker.json` |
| `.workspace/<env>/wiki.db` | Environment-specific SQLite: screenshots, tags, ocr_lines, entities, embeddings, FTS5 |
| `.workspace/<env>/wiki.ndjson` | flat dump of all records |
| `.workspace/<env>/tags_index.json` | tag frequencies + co-occurrence edges |
| `.workspace/<env>/thumbnails/` | 320px JPEG thumbnails (optional) |

---

## Tracker format (`_tracker.json`)

A per-file registry plus a per-run summary. Each entry is keyed by the file's
absolute path; `processed_at == null` means *unprocessed*.

```json
{
  "files": {
    "/abs/path/Screenshot 2026-08-13 at 20.10.40.png": {
      "filename":      "Screenshot 2026-08-13 at 20.10.40.png",
      "mtime_iso":     "2026-08-13T18:10:54.850418+00:00",
      "processed_at":  "2026-08-16T09:28:00.000000+00:00",
      "quality_score": 5,
      "status":        "ok"
    }
  },
  "runs": {
    "last_run_at":        "2026-08-20T13:10:21+00:00",
    "last_count_param":   10,
    "total_files":        2027,
    "processed":          5,
    "unprocessed":        2022,
    "new_this_run":       0,
    "processed_this_run": 0,
    "errors_this_run":    0,
    "status":             "nothing-to-process"
  }
}
```

- `status` per file: `pending` (not yet done), `ok` / `fail` (just classified),
   `backfilled` (auto-seeded from an existing `_annotations.jsonl` line).
- `runs.status`: `completed`, `nothing-to-process`.
- Old flat-index checkpoints (`last_processed_index`, …) are detected and ignored;
  the registry is rebuilt from the folder + existing annotations on first run.

---

## Annotation format (`_annotations.jsonl`)

The raw per-image output of the classifier — one JSON object per line, appended
as each image finishes and never rewritten (the durable source of truth; the
tracker and `wiki.db` are derived from it).

```json
{
  "filename":        "Screenshot 2026-07-06 at 16.04.29.png",
  "filepath":        "/abs/path/Screenshot 2026-07-06 at 16.04.29.png",
  "mtime_iso":       "2026-07-06T14:04:34.841885+00:00",
  "tags":            ["conference-call", "video-call-screen", "participant-grid"],
  "OCR_text":        ["Microsoft Teams", "Welcome to Agentic Value — Team Kick-off", "SAP"],
  "entities":        ["Microsoft Teams", "SAP", "Doerflinger, Joerg"],
  "caption":         "A Microsoft Teams meeting with a 34-participant grid shows a cartoon living room with the SAP logo.",
  "quality_score":   5,
  "embedding_vector": [768 float32 values from nomic-embed-text]
}
```

Field glossary:

- `filename` / `filepath` — source image name and its absolute path (the tracker
   keys by `filepath`).
- `mtime_iso` — source file mtime in ISO-8601 UTC; drives KB re-ingest (a newer
   `mtime` means "re-ingest this row").
- `tags[]` — scene tags chosen from the environment's `TAG_LIST` (at least one if
   anything matched).
- `OCR_text[]` — visible text lines, produced by the **vision model** (not an
   external OCR engine).
- `entities[]` — notable named entities the model saw on screen.
- `caption` — a one-sentence plain-English description of the screenshot.
- `quality_score` — integer 1–5, the model's confidence the shot is crisp/readable
   (5 = clearly readable, 1 = blurry/unusable).
- `embedding_vector` — a 768-dim float vector from `nomic-embed-text`, stored in
   `wiki.db` (as a `float32` blob) for planned semantic search.
