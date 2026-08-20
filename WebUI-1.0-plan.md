# WebUI 1.0 — Plan

A simple, extensible single-page web UI to visualize the screenshot-knowledgebase
pipeline: a **backlog dashboard**, a **timeline**, and a **tags panel**.

## Confirmed decisions
- **Stack:** stdlib `http.server` backend + vanilla JS/HTML/CSS. Zero deps, no
  build step, Python 3.9-safe.
- **Progress model:** a **funnel of pipeline stages**, each shown as a % of the
  total (`TOTAL = _tracker.json` total_images, 2027). Stages read live from their
  source files.
- **ETA / time-equivalent backlog:** `avg_latency` over **ok** telemetry runs
  (`vision_latency_s`) × `(TOTAL − max ok count)` → hours + projected finish; a
  latency sparkline shows per-run latency (incl. the 663 s outlier).
- **Layout:** one scrolling page, top→bottom: Backlog → Timeline → Tags.
- **Images:** placeholder tile + `file://` "open original" link, degrading to a
  copyable mono path if the browser blocks `file://`.
- **README:** add a short `## WebUI` run section after building.

## Data sources → what each drives
| File | State | Feed |
|---|---|---|
| `_tracker.json` | stale/flat (`total_images:2027`) | total denominator |
| `telemetry.log` | 12 lines (11 ok / 1 fail) | progress + ETA + latency sparkline |
| `_annotations.jsonl` | 12–13 full records | timeline detail (OCR/entities/tags) |
| `exports/wiki.ndjson` | 5 records (stale) | lightweight timeline rows / wiki stage |
| `exports/tags_index.json` | top_tags + edges | tags panel |
| `exports/thumbnails/` | empty | image thumbs (placeholder now) |

Note: `telemetry.log` is the most authoritative "what actually happened" and is
ahead of both `_tracker.json` and `wiki.ndjson`. Progress is computed per-stage
from each source; the funnel narrows because stages are cumulative pipeline outputs
at different latencies.

## Funnel stages (denominator TOTAL, default 2027)
| Stage | Source (read fresh per request) | Now |
|---|---|---|
| Scanned | `_tracker.json` runs.total_files (fallback flat total_images) | 2027 |
| Vision attempts | `telemetry.log` line count | 12 |
| Vision ok | telemetry status=ok | 11 |
| Annotated | `_annotations.jsonl` line count | 13 |
| Wiki-ingested | `exports/wiki.ndjson` line count | 5 |

Status chips: `ok · fail · pending = TOTAL − max ok`.

## Backend — `app/server.py` (stdlib, 3.9-safe)
- `http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`.
- Re-parses all source files **fresh per request** (tracks a live run, no restart).
- Resolves JSON/JSONL via absolute paths from `__file__` (CWD-independent).
- JSONL parse: line-by-line `json.loads`, skip blank/malformed.
- Endpoints:
   - `GET /` → `app/index.html`
   - `GET /app.js`, `/style.css` → static
   - `GET /api/overview` → `{stages:[{name,count,pct,color}], avg_latency_s,
     remaining, eta_seconds, eta_human, projected_finish_iso, sparkline:[],
     status_counts:{ok,fail,pending}, total}`
   - `GET /api/timeline` → merged rows newest-first (join `filename` across
     annotations ↔ telemetry ↔ wiki.ndjson), capped with `has_more`. Each row:
     `{filename, mtime_iso, status(ok/fail/none), quality, caption, tags[],
     ocr_text[](truncated), entities[], telem_latency_s, has_thumb, original_path}`.
   - `GET /api/record?filename=` → full untruncated record.
   - `GET /api/tags` → passthrough `top_tags` + `edges`.
   - `GET /api/telemetry` → raw telemetry rows (sparkline detail).
   - `GET /thumb/<filename>` → serve from `exports/thumbnails/` or 404 (placeholder).
- CLI: `python3 app/server.py [--port 8000]`.

## Frontend — `app/index.html` + `app/app.js` + `app/style.css`
Single scrolling page, dark monospace theme, no CSS framework:
1. **Backlog dashboard** — funnel bars (from `/api/overview`), ETA/status chips,
   inline-SVG latency sparkline. Auto-poll every 5s (toggleable).
2. **Timeline** — list sliced to first ~100 rows + "load more". Row = thumb
   placeholder + filename + mtime + status dot + quality + caption + tag chips +
   truncated OCR. Filters: tag dropdown, status radio, free-text search.
   Click row → `/api/record` → expand panel with full OCR/entities/tags +
   "open original" link (degrades to copyable path).
3. **Tags panel** — `top_tags` as sorted horizontal bars; `edges` as a weighted
   co-occurrence list. Clicking a tag chip applies the timeline filter.

## Extensibility hooks
- `STAGES` (config array in app.js + fields in `/api/overview`): adding a new
  pipeline stage is one config line + one field.
- Filters via a generic `{tag,status,q}` state object, not hardcoded.
- `top_tags`/`edges` passthrough → a future graph lib drops in without touching
  the data layer.

## Verification
- `python3 app/server.py --port 8000`, open `http://localhost:8000`.
- `curl localhost:8000/api/overview` → funnel matches the table (vision ok=11,
  annotated=13, wiki=5, total=2027).
- `curl localhost:8000/api/timeline` → merged rows incl. the 1 `fail`.
- `curl localhost:8000/api/tags` → top_tags + edges.
- Visual: funnel narrows, sparkline shows 663 s outlier, row expands full OCR,
  tag chip filters, "open original" degrades to copyable path.
