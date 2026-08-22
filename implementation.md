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
- **Working dir (git repo):** `/Users/I778444/git/screenshot_annotation`
- **Pick one source of truth for the pipeline code.** iCloud has `classify_images.py`,
  `kb/build_kb.py`, `kb/config.py` and output files; the git repo has an empty
  `app/`, `queries/`, `exports/` and its own `kb/config.py`. The two copies **drift**.
  Recommend: keep **code in the git repo**, keep **data + generated outputs in iCloud**,
  and stop editing the iCloud copy of the scripts. (Decide before starting.)
- **Model config lives in `kb/config.py`.** Current active vision model
  `muse-glimmer:30b-mlx` is slow (~90 s/img). See §1 cost.

---

## 1. Cost reality (read this first)
- `classify_images.py` runs ~**90 s/image** with the 30B vision model.
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
- **Cluster** embeddings via `hierarchical, min_cluster_size=3`
  (already declared in `kb/config.py` → `CLUSTER_KWARGS`).
- Each cluster = a candidate **topic**. Small/loner clusters fold into "misc".
- **Verify:** print cluster sizes + top tags per cluster.

### Stage 3 — Vision extract per image (the expensive atom, resumable)
Use existing `classify_images.py` logic, but:
- Run over **uniques only**, in **mtime order**.
- Vision prompt → `{caption, OCR_text[], entities[], tags[], quality 1–5}`.
- Keep `embedding_vector` (768-dim, nomic).
- Keep checkpointing `_tracker.json`. Per-file telemetry now lives alongside
   in the shared `tracker.py` module: each entry gains `started_at`, `finished_at`,
   `vision_latency_s`, `status` (`ok`/`fail`/`error`), `error`, plus `ingested_at`
   and `thumb_at` from `build_kb.py`. The old `telemetry.log` is retired.
   `build_kb.py` is incremental: it re-ingests only new/changed records (mtime newer
   than `ingested_at`) and adopts thumbnails already on disk without re-running `sips`
- Optionally: run cheap **pre-screen** (Stage-2 embedding + a fast model) and only
  invoke the 30B model on cluster *representatives*; other images get the cheaper model.
- Output: `_annotations.jsonl` (append, deterministic mtime order → safe re-run).
- **Verify:** `--count 30` smoke run; confirm valid JSON + non-empty tags; watch latency.

### Stage 4 — Ingest into SQLite (fix `kb/build_kb.py`)
Existing design is good (SQLite FTS5 + embedding blob + tag co-occurrence +
monthly histogram). **It currently does not run.** Fix before trusting:
- `NameError: OUTPUT_DIR` (lines ~264, ~304) — define `OUTPUT_DIR = SCRIPT_DIR / "exports"`.
- Replace mixed `cur` / `conn` usage with a **single cursor** consistently.
- Delete the dead thumbnail block (it submits `recs[i].get("filepath","")` strings into
  the pool — a no-op) or implement real thumbnails with `sips`.
- **Add tables for the wiki layer:**
  - `clusters (id, label, size, embedding_blob)`
  - `wiki_pages (id, slug, title, markdown, source_sids[], created_at)`
  - `timeline_digests (date_key, markdown, sids[])`
- **Verify:** `python3 kb/build_kb.py --no-thumbs` builds `kb/data/wiki.db`
  + `exports/wiki.ndjson` + `exports/tags_index.json` with no error.

### Stage 5 — Synthesis → the LLM wiki (the meaningful output)
This is what actually becomes a knowledgebase, not a metadata dump.
- **Per cluster (topic):** send representative `caption + OCR + entities` to the LLM →
  produce one **wiki page** (markdown):
  `# Title`, 2–4 sentence summary, key facts/bullets, `Timeline: YYYY-MM → YYYY-MM`,
  `Source images: [sids]`, `Tags:`.
- **Global index** `exports/wiki/index.md`: list of topic pages + counts.
- **Timeline** `exports/wiki/timeline.md`: day/week/month digests from `mtime`
  (reuse `monthly_histogram`), each pointing to the source sids.
- **Search layer** (already the `app/`/`queries/` intent): FTS5 for keyword,
  embedding cosine for semantic. Expose at least a **CLI query**; a small web
  viewer in `app/` is a nice-to-have, not required.
- Final deliverable = **markdown pages** (portable, editable, the "LLM wiki").
- **Verify:** read 3–5 generated pages for coherence; query a keyword + a semantic
  query and confirm it returns sensible sids.

---

## 3. Storage / artifacts
- `kb/data/wiki.db` — source of truth, SQLite FTS5 + `embedding_vector` blob.
- `exports/` — `wiki/` markdown pages+index+timeline, `wiki.ndjson`,
  `tags_index.json` (co-occurrence graph), `thumbnails/` (optional).
- `data/manifest.jsonl` — dedup record.
- All paths come from `kb/config.py`; do not hardcode in stage scripts.

---

## 4. Environment / gotchas
- **python3 is 3.9** (system): no `match`, no PEP 604 `X | Y` unions at runtime,
  no `list[...]`/`dict[...]` generics evaluated at runtime. Keep stage scripts stdlib-only
  and 3.9-safe (the existing scripts already are — maintain that).
- **Ollama** hosts: `muse-glimmer:30b-mlx` (vision, slow), `qwen3.8:27b-mlx`
  (active), `nomic-embed-text` (embed). Confirm the model name in `config.py`
  matches an installed one before a run; a wrong name = every call fails silently
  to `None` and you get empty tags, not an error.
- **tesseract = eng only.** Vision-model OCR handles English; non-English text won't
  OCR well — note as a known limitation, don't block on it.
- **HEIC → jpeg** via `/usr/bin/sips` is already handled in `classify_images.py`.
  No ImageMagick installed — use `sips` / `ffmpeg` only.
- **Two copies of the codebase drift** (iCloud vs git). Decide source of truth (§0)
  before editing so you don't fix one copy and run the other.
- **iCloud path** can be stale mid-run if synced; run stage scripts from the local
  checkout, copy/`open` the iCloud dir once at start, don't rely on live sync.
- `.mov` (7 files): skip for v1; optional ffmpeg keyframe extraction is a later
  enhancement, not on the critical path.

---

## 5. Suggested execution order / checkpoints
1. Fix `kb/build_kb.py` so it runs on the existing 5 annotations (fast, validates schema).
2. Write Stage 1 (dedup) → get true unique count; **report it** before any vision run.
3. Stage 2 embeddings+clusters on uniques → sanity-check cluster sizes.
4. Stage 3 vision extract via `--count` ramp: 30 → 100 → full, monitoring
   `_tracker.json` (per-file `vision_latency_s`, `status`, `error`) for latency
    and empty-tag/error rate.
5. Stage 4 re-ingest into SQLite (with new wiki tables).
6. Stage 5 synthesis → markdown wiki; eyeball quality; iterate on the synthesis prompt.

Each stage is resumable and verifiable on its own; don't wire Stage 5 until
Stages 1–3 produce a real `_annotations.jsonl`.

---

## 6. Open questions (decide before coding Stage 5+)
- Web viewer (`app/`) or CLI-only query for v1? (Recommend CLI-only first.)
- One source of truth for code: git repo or iCloud? (Recommend git repo.)
- Vision volume model: cheap model for all uniques vs 30B only on cluster
  representatives? (Recommend the latter for speed.)
