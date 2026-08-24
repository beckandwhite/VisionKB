# Screenshot Knowledgebase — Implementation Plan

## Goal
Turn ~2028 heterogeneous screenshots in iCloud `Screenshots/` into a local,
**searchable, timeline-aware knowledgebase** whose final human-facing artifact is an
**LLM-written wiki** (markdown topic pages + a dated timeline + an index).

Raw per-image metadata (the current scaffold) is only step 1. The meaningful
knowledgebase is the **synthesized layer on top**: cluster images into topics, then
have an LLM write one wiki entry per topic, plus per-day digests.

---

## 0. Decisions / source of truth
- **Data dir (read-only source):**
  `~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`
- **Working dir (git repo):** `/Users/t/git/screenshot_annotation`
- **Pipeline code lives in the git repo.** Runtime data and generated outputs are
  isolated under `.workspace/<env>/`; the root `exports/` directory is not used.
- **Model config lives in the active environment's `config.json`,** loaded by
  `config_loader.py`. Current active vision model
  `muse-glimmer:30b-mlx` is slow (~90 s/img). See §1 cost.

---

## 1. Cost reality (read this first)
 - `backend.py` runs ~**90 s/image** with the 30B vision model.
  2028 images ⇒ **~50 hours**. Not viable as-is.
- Ollama is effectively **single-stream**; Python "concurrency" does **not** speed up
  vision (requests just queue). Real levers:
  1. **Dedup first** — many files are exact copies (`..._n 1.jpg`, `..._n 2.jpg`,
     dup png+heic). Cuts work, prevents 3x wasted vision calls.
  2. **Smaller/cheaper model** for the volume pass; reserve the 30B model for
     cluster *representatives* only.
  3. **Skip near-dupes by embedding** before spending a vision call.
- Budget: aim to run vision on **the deduplicated set** (~1200–1600 uniques), not 2028.

---

## 2. Pipeline (5 stages, each independently runnable + resumable)

### Stage 1 — Ingest, normalize, dedup → `manifest`
- Scan `SCREENSHOT_ROOT` for supported exts: `.png .jpg .jpeg .heic` (skip `.mov`
  for v1; optional ffmpeg keyframe extraction later).
- **Dedup:**
  - exact: `sha256` of file bytes.
  - near: downscaled perceptual hash (or cheap embedding cosine < 0.98).
  - Collapse `..._n 1.jpg` / `..._n 2.jpg` Facebook dupes (same base path).
- Emit `data/manifest.jsonl`: `{path, sha256, phash, ext, mtime_iso, unique:true|false}`.
- **Verify:** count uniques vs total; spot-check that dupes are grouped.

### Stage 2 — Embedding pre-pass + clusters (cheap, no vision)
- Embed each **unique** image once (embed model = `nomic-embed-text`).
- **Cluster** embeddings via `hierarchical, min_cluster_size=3` once clustering
  configuration is added to the active environment.
- Each cluster = a candidate **topic**. Small/loner clusters fold into "misc".
- **Verify:** print cluster sizes + top tags per cluster.

### Stage 3 — Vision extract per image (the expensive atom, resumable)
 Use existing `backend.py` logic, but:
- Run over **uniques only**, in **mtime order**.
- Vision prompt → `{caption, OCR_text[], entities[], tags[], quality 1–5}`.
- Keep `embedding_vector` (768-dim, nomic).
- Keep checkpointing `_tracker.json`. Per-file telemetry now lives alongside
   in the shared `tracker.py` module: each entry gains `started_at`, `finished_at`,
   `vision_latency_s`, `status` (`ok`/`fail`/`error`), `error`, plus `ingested_at`
    and `thumb_at` from `backend.py`. The old `telemetry.log` is retired.
     `backend.py` is incremental: it re-ingests only new/changed records (mtime newer
   than `ingested_at`) and adopts thumbnails already on disk without re-running `sips`
- Optionally: run cheap **pre-screen** (Stage-2 embedding + a fast model) and only
  invoke the 30B model on cluster *representatives*; other images get the cheaper model.
- Output: `_annotations.jsonl` (append, deterministic mtime order → safe re-run).
- **Verify:** `--count 30` smoke run; confirm valid JSON + non-empty tags; watch latency.

 ### Stage 4 — Ingest into SQLite (folded into `backend.py`, runs per image)
Existing design is good (SQLite FTS5 + embedding blob + tag co-occurrence +
monthly histogram). Keep its output paths environment-specific:
- Replace mixed `cur` / `conn` usage with a **single cursor** consistently.
- Delete the dead thumbnail block (it submits `recs[i].get("filepath","")` strings into
  the pool — a no-op) or implement real thumbnails with `sips`.
- **Add tables for the wiki layer:**
  - `clusters (id, label, size, embedding_blob)`
  - `wiki_pages (id, slug, title, markdown, source_sids[], created_at)`
  - `timeline_digests (date_key, markdown, sids[])`
 - **Verify:** `python3 backend.py --rebuild-kb --no-thumbs` builds
  `.workspace/<env>/wiki.db` + `.workspace/<env>/wiki.ndjson` +
  `.workspace/<env>/tags_index.json` with no error.

### Stage 5 — Synthesis → the LLM wiki (the meaningful output)
This is what actually becomes a knowledgebase, not a metadata dump.
- **Per cluster (topic):** send representative `caption + OCR + entities` to the LLM →
  produce one **wiki page** (markdown):
  `# Title`, 2–4 sentence summary, key facts/bullets, `Timeline: YYYY-MM → YYYY-MM`,
  `Source images: [sids]`, `Tags:`.
- **Global index** `.workspace/<env>/wiki/index.md`: list of topic pages + counts.
- **Timeline** `.workspace/<env>/wiki/timeline.md`: day/week/month digests from `mtime`
  (reuse `monthly_histogram`), each pointing to the source sids.
- **Search layer** (already the `app/`/`queries/` intent): FTS5 for keyword,
  embedding cosine for semantic. Expose at least a **CLI query**; a small web
  viewer in `app/` is a nice-to-have, not required.
- Final deliverable = **markdown pages** (portable, editable, the "LLM wiki").
- **Verify:** read 3–5 generated pages for coherence; query a keyword + a semantic
  query and confirm it returns sensible sids.

---

## 3. Storage / artifacts
- `.workspace/<env>/wiki.db` — environment-specific source of truth, SQLite FTS5 +
  `embedding_vector` blob.
- `.workspace/<env>/` — `wiki/` markdown pages+index+timeline, `wiki.ndjson`,
  `tags_index.json` (co-occurrence graph), and `thumbnails/` (optional).
- `data/manifest.jsonl` — dedup record.
- All paths come from `config_loader.py`; do not hardcode in stage modules.

---

## 4. Environment / gotchas
- **python3 is 3.9** (system): no `match`, no PEP 604 `X | Y` unions at runtime,
  no `list[...]`/`dict[...]` generics evaluated at runtime. Keep stage scripts stdlib-only
  and 3.9-safe (the existing scripts already are — maintain that).
- **Ollama** hosts: `muse-glimmer:30b-mlx` (vision, slow), `qwen3.8:27b-mlx`
  (active), `nomic-embed-text` (embed). Confirm the model name in the active
  environment configuration matches an installed one before a run.
- **tesseract = eng only.** Vision-model OCR handles English; non-English text won't
  OCR well — note as a known limitation, don't block on it.
- **HEIC → jpeg** via `/usr/bin/sips` is already handled by the pipeline's classifier module.
  No ImageMagick installed — use `sips` / `ffmpeg` only.
- **Keep the git checkout as the code source of truth.** Runtime data and generated
  outputs belong under the selected `.workspace/<env>` directory.
- **iCloud path** can be stale mid-run if synced; run stage scripts from the local
  checkout, copy/`open` the iCloud dir once at start, don't rely on live sync.
- `.mov` (7 files): skip for v1; optional ffmpeg keyframe extraction is a later
  enhancement, not on the critical path.

---

## 5. Suggested execution order / checkpoints
1. Validate `backend.py` against the existing annotations.
2. Write Stage 1 dedup and report the true unique count before vision work.
3. Run the embedding pre-pass and inspect cluster sizes.
4. Run per-source works in bounded batches, monitoring the generic tracker.
5. Run Work 5 and inspect duplicate groups before selecting canonical files.
6. Add clustering and wiki synthesis only after the work/result contract is stable.

Each work should be independently runnable and resumable. Dataset-wide producers
such as Work 5 should write explicit run metadata and remain separate from the
per-source queue.

---

## 6. Open questions
- Should duplicate groups be automatically collapsed or only reviewed by a later consumer?
- Should result JSONL retain all attempts or only the latest result per source version?
- Should the frontend expose one work at a time or a combined source view?
- Which vision model is affordable for the full Work 1–3 backlog?























# VisionKB Generic Work Implementation Plan

## Goal

Turn a folder of pictures into a reusable, searchable data lake. Each source picture can be processed by multiple independent works. Analytical results are stored separately from queue state so new work types do not require tracker schema changes.

The current implementation is intentionally breaking: legacy tracker files and frontend assumptions are not compatibility targets. Runtime data belongs under `.workspace/<env>/`; source pictures remain in the configured source directory.

## Architecture

```text
source folder
    |
    +--> tracker.py: sources + independent (source, work) tasks
    |
    +--> backend.py: per-source worker runner
    |       |
    |       +--> work1.py: generic vision query -> work1.jsonl
    |       +--> work2.py: OCR -> work2.jsonl
    |       +--> work3.py: classifier -> work3.jsonl
    |       +--> work4.py: thumbnails -> thumbnails/*.jpg
    |
    +--> work5.py: dataset-wide exact duplicate grouping
                    -> duplicatefinder.jsonl
```

`backend_generic.py` is not part of the active architecture. Its vision prompt is synchronized with Work 1, but its legacy tracker/API implementation remains separate.

## Tracker contract

`_tracker.json` uses schema version 2:

```json
{
  "schema_version": 2,
  "sources": {
    "/absolute/path/image.png": {
      "source_key": "/absolute/path/image.png",
      "filename": "image.png",
      "created_at": "ISO timestamp",
      "modified_at": "ISO timestamp",
      "discovered_at": "ISO timestamp",
      "missing": false
    }
  },
  "tasks": {
    "stable task id": {
      "source_key": "/absolute/path/image.png",
      "work_name": "work1",
      "input_modified_at": "ISO timestamp",
      "worker_started_at": "ISO timestamp",
      "worker_id": "host:pid",
      "worker_finished_at": "ISO timestamp"
    }
  },
  "runs": {}
}
```

The source key is the canonical absolute path. Creation time uses macOS `st_birthtime` when available and falls back to `st_ctime`; modification time uses `st_mtime`. A changed modification time resets the task lifecycle for the new source version.

The tracker stores lifecycle telemetry only. Work-specific output, status, errors, model metadata, and retry details belong in result artifacts.

## Work definitions

Works are configured per environment in `config.json`:

- `name`: stable work identifier.
- `scope`: `per_source` or `dataset`.
- `handler`: Python handler name.
- `output`: `jsonl`, `files`, or `none`.
- `result_file` or `output_dir`: work artifact location.
- `enabled`: whether the work is active.

Adding a new per-source work should require a configuration entry and handler, not a tracker change.

## Per-source works

### Work 1: generic vision query

File: `work1.py`

Default prompt:

```text
What is on this picture? Describe the important visible content.
```

Output: `.workspace/<env>/work1.jsonl`.

### Work 2: OCR

File: `work2.py`

Asks the vision model to extract visible text and returns a JSON `text` array.

Output: `.workspace/<env>/work2.jsonl`.

### Work 3: classifier

File: `work3.py`

Asks the vision model to classify the picture and return classification data such as class and confidence.

Output: `.workspace/<env>/work3.jsonl`.

### Work 4: thumbnails

File: `work4.py`

Generates a 320px JPEG at:

```text
.workspace/<env>/thumbnails/<source-stem>.jpg
```

The JPEG is the result. No Work 4 JSONL file is written. The Work 4 task records only worker start, worker ID, and worker finish timestamps.

## Dataset-wide Work 5

File: `work5.py`

Work 5 is not a per-picture queue task. It scans the complete configured picture set, hashes files with SHA-256, and groups exact duplicates.

```bash
python3 work5.py -env DEV
```

Output:

```text
.workspace/<env>/duplicatefinder.jsonl
```

Each duplicate group includes a run ID, generation timestamp, algorithm, SHA-256 value, scanned source count, duplicate count, and source records containing absolute-path foreign keys and creation/modification timestamps. A later Python program can consume this artifact.

The operation is named Work 5; `duplicatefinder.jsonl` remains the downstream artifact filename.

## Worker behavior

`backend.py` performs the per-source loop:

1. Acquire the environment writer lock.
2. Reconcile the source directory into the source manifest.
3. Create tasks for enabled per-source works.
4. Select unfinished or stale tasks for the current source version.
5. Claim each task with worker identity and start timestamp.
6. Execute the configured handler.
7. Append analytical JSONL output or create file output.
8. Record worker completion in the tracker.
9. Save progress atomically.

`--count` counts work tasks, not pictures. A source with four enabled per-source works consumes four task slots. `--until HH:MM` provides a bounded run for nightly scheduling. The environment lock prevents concurrent writers.

A failed task is reset to unfinished so a later run can retry it. The failure itself belongs in an analytical result record when applicable; Work 4 has only its file output and task lifecycle.

## Dataset storage

```text
.workspace/<env>/
  config.json
  _tracker.json
  work1.jsonl
  work2.jsonl
  work3.jsonl
  thumbnails/
  duplicatefinder.jsonl
```

JSONL analytical results should include at least the source key, filename, source timestamps, work name, input modification timestamp, execution timestamps, output, and result status. Immutable attempts are preferred when retry history matters.

## Operational commands

Per-source processing:

```bash
python3 backend.py -env DEV --count 5
python3 backend.py -env DEV --until 06:00
```

Dataset-wide duplicate scan:

```bash
python3 work5.py -env DEV
```

The frontend is read-only and can be run independently, but its broader timeline still needs a separate migration to the generic result model.

## Future work

1. Add focused tests for source reconciliation, timestamp fallback, task IDs, changed-mtime requeueing, stale claims, and atomic tracker writes.
2. Add explicit result-attempt selection and retry commands.
3. Add a consumer for `duplicatefinder.jsonl` to choose canonical files or annotate duplicate groups.
4. Add content hashes to source metadata after the absolute-path contract is stable.
5. Add optional perceptual similarity or embedding-based near-duplicate grouping to Work 5.
6. Add clustering and embeddings as separate dataset/per-source works.
7. Build the wiki synthesis layer from Work 1, Work 2, Work 3, and Work 5 results.
8. Update the frontend to expose work selection and independent completion.

## Validation completed

- Python compilation passed for the generic tracker, runner, Work 1 through Work 5, shared helpers, frontend, and `backend_generic.py` syntax.
- Tracker fixture passed with one source and four independent tasks.
- Mocked dispatcher fixture passed with independent Work 1, Work 2, and Work 3 outputs.
- Work 4 fixture passed with an existing thumbnail preserved and task completion recorded without JSONL output.
- Work 5 fixture passed with two identical files producing one duplicate group.
- `git diff --check` passed.
