# Screenshot Knowledgebase

Turn a folder of screenshots into a local, searchable, **LLM-written wiki**.
Raw per-image metadata is extracted with a local vision model, then synthesized
into clustered topic pages + a timeline.

> Status: **experimental**. The extraction + ingestion pipeline runs; the
> clustering + LLM-wiki synthesis layers are still to be built — see
> [implementation.md](implementation.md).

---

## What it does

```
screenshots ──► pipeline.py ──► .workspace/<env>/_annotations.jsonl + wiki.db + exports
 (images)       (classify → ingest → index, one image at a time)
```

Each image becomes one JSON record: `tags[]`, `OCR_text[]`, `entities[]`,
 `caption`, `quality_score (1-5)` (the vision model's confidence that the
 screenshot is crisp and readable — 5 = clearly readable, 1 = blurry/unusable),
 and a 768-dim embedding. The ingestion step
builds an FTS5 full-text index, tag co-occurrence graph, and monthly histogram.

---

## Prerequisites

All on macOS. No `pip` install required — the scripts are **stdlib-only**.

| Requirement | Where | Needed for |
|---|---|---|
| **Python 3.9+** (3.9.6 verified) | `/usr/bin/python3` | everything |
| **Ollama** + models below | homebrew | vision + embeddings |
| **sips** | `/usr/bin/sips` (preinstalled) | HEIC→JPEG, thumbnails |
| ffmpeg / tesseract | homebrew | optional / not used by core path |

### Ollama models (must be pulled)

```bash
ollama pull muse-glimmer:30b-mlx     # vision model  (slow: ~90s/image)
ollama pull nomic-embed-text         # embedding model
```

Start the server and confirm it is up:

```bash
ollama serve &                          # or: it may already be running
curl -s localhost:11434/api/tags | jq   # list models; expect the two above
```

> A wrong/missing model name makes vision calls fail **silently** (empty tags,
> not an error). Verify the name matches `ollama list`.

---

## Layout

```
 screenshot_annotation/
 ├── tracker.py             shared tracker module (registry + telemetry + KB stamps)
 ├── pipeline.py             # single entry point: classify + ingest + exports
 ├── config_loader.py        # environment configuration and artifact paths
 ├── .workspace/<env>/        # config + isolated annotations/tracker/KB artifacts
 ├── classify_images.py       # classifier implementation used by pipeline.py
 └── build_kb.py              # KB implementation used by pipeline.py
```

Images themselves live in the configured source folder. Select an environment
with `-env` (default `PRD-iCloud-Screenshots`):

```bash
python3 pipeline.py -env QA
python3 pipeline.py -env PRD-iCloud-Screenshots
```

Images themselves commonly live in iCloud:
`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`

`pipeline.py` defaults to the active environment's `source_dir`; point it elsewhere with
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
python3 pipeline.py --count 5

# use the environment's configured limit (QA=100)
python3 pipeline.py -env QA

# scan a different folder with --screenshot-dir
python3 pipeline.py --screenshot-dir '~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/' --count 5

# classify all remaining unprocessed files (no limit)
python3 pipeline.py                    # configured limit; --count 0 = all remaining
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
python3 pipeline.py -env DEV --rebuild-kb --no-thumbs
python3 pipeline.py -env DEV --rebuild-kb --force
```

Produces `.workspace/<env>/wiki.db`, `.workspace/<env>/wiki.ndjson`,
and `.workspace/<env>/tags_index.json`; thumbnails are stored in
`.workspace/<env>/thumbnails/`.

`pipeline.py` is incremental: completed files are skipped on later runs. A
second writer for the same environment exits cleanly while another run holds
the environment lock; use `--wait` when waiting is preferred.

For a bounded nightly run, stop before starting another image at the next local
06:00 deadline:

```bash
python3 pipeline.py -env PRD-iCloud-Screenshots --until 06:00
```

For cron, use `run.sh` with an explicit environment. Example copy-paste entry:

```cron
0 0 * * * /Users/t/git/VisionKB/run.sh -env PRD-iCloud-Screenshots --until 06:00 >> /Users/t/git/VisionKB/pipeline.log 2>&1
```

Cron does not run reliably while the Mac sleeps, and Ollama plus the configured
source folder must be available. At roughly 90 seconds per image, 2,000 images
requires about 50 hours of model time before failures or duplicates.

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
 see implementation.md, Stage 5.)

 ### 4. WebUI (timeline + backlog dashboard)

 A dependency-free single-page viewer over the pipeline artifacts (stdlib
 `http.server` + vanilla JS; no build). Read-only — it never writes or touches
 the pipeline scripts; every source file is re-read per request, so a live
`pipeline.py` run shows up without a restart.

 ```bash
python3 app/server.py -env PRD-iCloud-Screenshots --port 8000 --open              # http://127.0.0.1:8000
python3 app/server.py -env PRD-iCloud-Screenshots --port 8000 --open
# The UI reads the selected environment's tracker, annotations, exports, and thumbnails.
 ```

The WebUI defaults to `PRD-iCloud-Screenshots`. Use the same `-env` value as the pipeline so the
UI and processor point at the same isolated workspace, for example:

```bash
python3 pipeline.py -env PRD-iCloud-Screenshots --count 5
python3 app/server.py -env PRD-iCloud-Screenshots --port 8000 --open
```

 Three sections, top → bottom:
 - **Backlog** — funnel of pipeline stages as % of the tracker total
   (`_tracker.json` `total_files` ≈ 2027): *Scanned → Vision attempts →
   Vision ok → Annotated → Wiki-ingested*, plus a time-equivalent backlog
   (`avg latency × remaining` → ETA + projected finish), status chips
   (`ok / fail / error / pending`, plus `ingested` + `thumbnails` from the builder), and a per-run latency sparkline.
 - **Timeline** — rows joined from `_annotations.jsonl` + the tracker (telemetry + error) +
   `wiki.ndjson` (filename key), newest first; filters by tag / status /
   free-text search; click a row to expand full OCR / entities / tags and an
   "open original" link.
 - **Tags** — `top_tags` bars + `edges` co-occurrence list from
  `tags_index.json`; clicking a tag filters the timeline.

 Thumbnails render live once `thumbnails/` is populated
(`python3 pipeline.py` without `--no-thumbs`); until then rows show a
 placeholder. See [WebUI-1.0-plan.md](WebUI-1.0-plan.md) for the design.

---

## Gotchas (read these first)

- **`pipeline.py` defaults to the active environment's source folder**
   (`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`); pass
   `--screenshot-dir` to scan elsewhere. Outputs (`_tracker.json`,
   `_annotations.jsonl`) are written next to the script, not into
   the scanned folder. The existing `_annotations.jsonl` is real history (5 records);
   its old flat-index `_tracker.json` is auto-migrated — the registry is rebuilt from
   the folder + those annotations on the first run.
- **Environment selection matters.** Use `-env` consistently for `pipeline.py`
  and `app/server.py`; all annotations, tracker state, database, exports, and
  thumbnails are isolated under `.workspace/<env>/`.
- **Cost: ~90 s/image with muse-glimmer:30b.** Ollama is effectively single-stream,
  so Python "concurrency" won't speed up vision. For the full ~2,000 images, see the
  dedup + cheaper-model strategy in implementation.md.
- **python3 is 3.9**: no `match`, no runtime `X | Y` unions. Keep scripts stdlib-only.
- **tesseract is english-only**; the vision model handles OCR, but non-English text
  won't be read well.

---

## Data files

| File | Purpose |
|---|---|
| `_annotations.jsonl` | one JSON record per image (append, never rewritten) |
| `_tracker.json` | per-file registry (filename + `mtime_iso` + `processed_at`) and run summary — the progress ledger |
| `tracker.py` | shared tracker module used by the pipeline's classifier and KB stages, plus `app/server.py` |
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
