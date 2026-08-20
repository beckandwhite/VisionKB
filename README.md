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
screenshots ──► classify_images.py ──► _annotations.jsonl ──► kb/build_kb.py ──► wiki.db (SQLite FTS5)
 (images)        (vision + embed)       (per-image JSON)        (ingest + index)   + exports/*.json/ndjson
```

Each image becomes one JSON record: `tags[]`, `OCR_text[]`, `entities[]`,
`caption`, `quality_score (1-5)`, and a 768-dim embedding. The ingestion step
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
├── classify_images.py     # Stage: vision + embedding → _annotations.jsonl
├── kb/
│   ├── config.py          # intended shared config (see Gotcha below)
│   └── build_kb.py        # Stage: ingest → SQLite FTS5 + exports
├── _annotations.jsonl      # output: one JSON record per image
├── _tracker.json           # per-file registry + run summary (progress ledger)
├── telemetry.log          # per-file latency / status log
├── kb/data/wiki.db        # output: SQLite DB (FTS5 + embeddings)
└── exports/               # output: wiki.ndjson, tags_index.json, thumbnails/
```

Images themselves live in iCloud:
`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`

`classify_images.py` defaults to that folder; point it elsewhere with
`--screenshot-dir` (see "How to use" below).

---

## How to use

### 1. Extract annotations (vision + embeddings)

```bash
# default folder is the iCloud Screenshots dir; classify the next 5 unprocessed
python3 classify_images.py --count 5

# scan a different folder with --screenshot-dir
python3 classify_images.py --screenshot-dir '~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/' --count 5

# classify all remaining unprocessed files (no limit)
python3 classify_images.py              # --count 0 = everything not yet done
```

`_tracker.json` is a **self-maintaining registry**, not a bare index. Each run:

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
- **Note on edited files:** a file already in the registry keeps its `processed_at`
  even if its mtime changes, so an in-place edit is **not** reprocessed by design
  (keyed by path, not by content/mtime). Delete its registry entry or its
  `_annotations.jsonl` line to force a re-process.
- Checkpointed atomically (`tmp` + `os.replace`) every 25 files and at end of run.
- `--screenshot-dir` sets the source folder (default: iCloud Screenshots above);
  outputs (`_tracker.json`, `_annotations.jsonl`, `telemetry.log`) are always written
  next to the script, not into the scanned folder.
- HEIC files are auto-converted via `sips`; oversized images are downscaled.
- Watch `telemetry.log` for per-image latency and `status` (ok/fail).

### 2. Build the knowledgebase (ingest → SQLite)

```bash
python3 kb/build_kb.py --no-thumbs    # skip thumbnails for speed
# or:
python3 kb/build_kb.py                # also generate 320px thumbnails via sips
```

Produces `kb/data/wiki.db`, `exports/wiki.ndjson`, `exports/tags_index.json`.

### 3. Query (FTS5 full-text)

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("kb/data/wiki.db")
for cap, in c.execute("SELECT caption FROM screenshots_fts "
                      "WHERE screenshots_fts MATCH 'terminal OR coding' LIMIT 10"):
    print(cap[:100])
PY
```

(Embedding/semantic search and the clustered **LLM-wiki** layer are planned —
see implementation.md, Stage 5.)

---

## Gotchas (read these first)

- **`classify_images.py` defaults to the iCloud folder**
   (`~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`); pass
   `--screenshot-dir` to scan elsewhere. Outputs (`_tracker.json`,
   `_annotations.jsonl`, `telemetry.log`) are written next to the script, not into
   the scanned folder. The existing `_annotations.jsonl` is real history (5 records);
   its old flat-index `_tracker.json` is auto-migrated — the registry is rebuilt from
   the folder + those annotations on the first run.
- **`kb/config.py` is not yet wired in.** Neither script imports it — model names
  and paths are hardcoded inside each script instead. Treat config.py as the
  *intended* source of truth and reconcile the two before a big run.
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
| `telemetry.log` | per-file latency + status (append-only) |
| `kb/data/wiki.db` | SQLite: screenshots, tags, ocr_lines, entities, embeddings, FTS5 |
| `exports/wiki.ndjson` | flat dump of all records |
| `exports/tags_index.json` | tag frequencies + co-occurrence edges |
| `exports/thumbnails/` | 320px JPEG thumbnails (optional) |

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
