#!/usr/bin/env python3
"""
WebUI backend for the screenshot knowledgebase (stdlib only, Python 3.9-safe).

Serves a single-page viewer over the pipeline artifacts. All source files are
re-parsed *fresh per request* so the UI tracks a live pipeline run without a
restart. Read-only: nothing here is written, never touches the pipeline scripts.

Telemetry and per-file progress now live in the shared tracker (_tracker.json);
this server reconstructs telemetry rows from it via tracker.telemetry_from_tracker().

Endpoints:
    GET /                          -> index.html
    GET /app.js / /style.css      -> static assets
    GET /api/overview              -> backlog status + ETA (remaining + speed window)
    GET /api/timeline             -> merged rows (annotations x tracker x wiki),
                                     newest first, capped with has_more
    GET /api/record?filename=     -> full untruncated record for one row
    GET /api/tags                 -> passthrough of the environment's tags_index.json
    GET /api/telemetry            -> reconstructed telemetry rows (from the tracker)
    GET /thumb/<file>             -> 320px thumbnail; ?original=1 -> full-res original

Usage:
    python3 frontend.py
    python3 frontend.py --port 8000 --open
"""

import argparse
import json
import math
import mimetypes
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker
import config_loader

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = SCRIPT_DIR

# Resolved in main() via config_loader.resolve_environment(..., auto_bootstrap=False).
# This module is read-only: it never creates a config, so an uninitialised
# environment fails fast with an actionable message instead of a silent copy.
CURRENT_ENV, TRACKER_PATH = None, None
ANNOT_PATH = WIKI_PATH = TAGS_PATH = THUMB_DIR = None
ENV_CONFIG = None

OCR_LINE_MAX = 100
OCR_LINES_MAX = 8
TIMELINE_DEFAULT_LIMIT = 150
TIMELINE_WINDOW_DEFAULT = 50
TIMELINE_WINDOWS = (50, 100, 150, 200)




# ---------------------------------------------------------------------------
# Loaders (fresh per request; defensive)
# ---------------------------------------------------------------------------

def load_tracker():
    """Return (sources, tasks, runs) from _tracker.json.
    Missing/corrupt/old-schema -> ({}, {}). The registry is read through the
    shared tracker module so the schema stays consistent with the writers."""
    payload = tracker.load_registry(TRACKER_PATH)
    return (payload.get("sources", {}), payload.get("tasks", {}),
            payload.get("runs", {}))


def load_work_results():
    """Return the latest valid configured JSONL result by work and source key."""
    results = {}
    if not ENV_CONFIG:
        return results
    for work in ENV_CONFIG.get("works", []):
        result_file = work.get("result_file")
        if not result_file or work.get("output") != "jsonl":
            continue
        path = ENV_CONFIG["env_dir"] / result_file
        by_source = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    source_key = record.get("source_key")
                    if source_key:
                        by_source[source_key] = record
        except OSError:
            pass
        results[work.get("name")] = by_source
    return results


def _work_result(results, work_name, source_key):
    return (results.get(work_name) or {}).get(source_key) or {}


def _duration_seconds(record):
    started = _iso_to_epoch(record.get("started_at"))
    finished = _iso_to_epoch(record.get("finished_at"))
    if started and finished and finished >= started:
        return round(finished - started, 2)
    return None


def _task_duration_seconds(task):
    started = _iso_to_epoch(task.get("worker_started_at"))
    finished = _iso_to_epoch(task.get("worker_finished_at"))
    if started and finished and finished >= started:
        return round(finished - started, 2)
    return None


def load_telemetry(work_name="work1"):
    """Reconstruct telemetry rows from the tracker (newest last).

    Each processed file yields one row: {timestamp, filename, vision_latency_s,
    tags_count, embedding_dims, status, error}. Backed by the shared tracker.
    """
    sources, tasks, _ = load_tracker()
    rows = []
    for task in tasks.values():
        if task.get("work_name") != work_name:
            continue
        source_key = task.get("source_key")
        source = sources.get(source_key, {})
        duration = _task_duration_seconds(task)
        if not source or duration is None:
            continue
        rows.append({
            "timestamp": task.get("worker_finished_at"),
            "filename": source.get("filename"),
            "source_key": source_key,
            "work_name": work_name,
            "vision_latency_s": duration,
            "status": "ok",
        })
    rows.sort(key=lambda row: row.get("timestamp") or "")
    return rows


def load_annotations():
    """Return {filename: record} from _annotations.jsonl. embedding stripped."""
    by_name = {}
    try:
        with open(ANNOT_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                rec.pop("embedding_vector", None)
                name = rec.get("filename") or "unknown"
                by_name[name] = rec
    except OSError:
        pass
    return by_name


def load_wiki():
    """Return {filename: record} from the environment's wiki.ndjson."""
    by_name = {}
    try:
        with open(WIKI_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                name = rec.get("filename") or "unknown-%s" % rec.get("sid", "")
                by_name[name] = rec
    except OSError:
        pass
    return by_name


def load_tags_index():
    """Return the raw tags_index.json object, or a minimal empty shape."""
    try:
        with open(TAGS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return {"total_screenshots": 0, "unique_tags": 0, "top_tags": [], "edges": []}


# ---------------------------------------------------------------------------
# Derived views
# ---------------------------------------------------------------------------

def _iso_to_epoch(iso_str):
    if not iso_str:
        return 0.0
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _query_float(qs, key):
    value = qs.get(key, [None])[0]
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _human_duration(seconds):
    seconds = int(round(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d > 0:
        return "%dd %dh" % (d, h)
    if h > 0:
        return "%dh %dm" % (h, m)
    return "%dm" % m


def _truncate_ocr(ocr_text):
    """Cap length + line count for in-list display; full text via /api/record."""
    lines = ocr_text or []
    out = [str(line)[:OCR_LINE_MAX] for line in lines[:OCR_LINES_MAX]]
    truncated = len(lines) > OCR_LINES_MAX
    return out, truncated


def _thumb_path_for(filename):
    """Absolute path to thumbnails/<stem>.jpg, or None if absent."""
    if not filename:
        return None
    stem = os.path.splitext(filename)[0]
    path = os.path.join(THUMB_DIR, stem + ".jpg")
    return path if os.path.isfile(path) else None


def _find_original(filename, source_key=None):
    """Absolute path to the full-res original for a thumbnail filename, or None.

    Used by /thumb/<file>?original=1 so a thumbnail can be clicked through to
    its source image. Looked up via the annotation record's "filepath"."""
    if not filename:
        return None
    sources, _, _ = load_tracker()
    source = sources.get(source_key) if source_key else None
    if source is None:
        source = next((item for item in sources.values()
                       if item.get("filename") == filename), None)
    path = source.get("source_key") if source else None
    if path and os.path.isfile(path):
        return path
    rec = load_annotations().get(filename)
    path = rec.get("filepath") if rec else None
    if path and os.path.isfile(path):
        return path
    return None


def build_overview(work_name="work1"):
    """Backlog status + ETA from the tracker.

    Reports how many pictures are still unprocessed and the estimated time
    left, derived from the speed of the most recently processed files.

      `processed` mirrors the backend's own "done" predicate (backend.py):
    a file counts as handled when it has a finished_at or processed_at stamp,
    so the backlog reflects the vision queue regardless of KB-layer stages.
    """
    sources, tasks, runs = load_tracker()
    telemetry = load_telemetry(work_name)
    results = load_work_results()

    active_sources = {key for key, source in sources.items()
                      if not source.get("missing")}
    total = len(active_sources)
    processed = sum(1 for task in tasks.values()
                    if task.get("work_name") == work_name
                    and task.get("worker_finished_at")
                    and task.get("source_key") in active_sources)
    remaining = max(total - processed, 0)

    # Speed = mean vision_latency_s of the most recent 5 processed files.
    # telemetry is newest-last; take trailing rows with a numeric latency.
    window = []
    for r in reversed(telemetry):
        lat = r.get("vision_latency_s")
        if isinstance(lat, (int, float)):
            window.append(float(lat))
        if len(window) >= 5:
            break
    window.reverse()
    has_speed = bool(window)
    avg_latency = (sum(window) / len(window)) if window else 0.0

    eta_seconds = remaining * avg_latency
    eta_human = _human_duration(eta_seconds) if remaining else "0m"
    projected_finish = (
        (datetime.now(tz=timezone.utc) + timedelta(seconds=eta_seconds)).isoformat()
        if (remaining and has_speed) else "")

    return {
        "environment": CURRENT_ENV,
        "total": total,
        "processed": processed,
        "remaining": remaining,
        "avg_latency_s": round(avg_latency, 2),
        "speed_window": len(window),
        "has_speed": has_speed,
        "eta_seconds": int(eta_seconds),
        "eta_human": eta_human,
        "projected_finish_iso": projected_finish,
    }


def _timeline_histogram(values, bucket_count=48):
    """Return a stable modification-time domain and density buckets."""
    valid = sorted(value for value in values if math.isfinite(value))
    if not valid:
        return None, None, []
    minimum, maximum = valid[0], valid[-1]
    if minimum == maximum:
        return minimum, maximum, [{"start": minimum, "end": minimum,
                                   "count": len(valid)}]

    width = (maximum - minimum) / bucket_count
    buckets = [{"start": minimum + i * width,
                "end": minimum + (i + 1) * width,
                "count": 0} for i in range(bucket_count)]
    for value in valid:
        index = min(int((value - minimum) / width), bucket_count - 1)
        buckets[index]["count"] += 1
    return minimum, maximum, buckets


def build_timeline(limit=None, offset=0, status_filter=None, tag_filter=None,
                   query=None, mtime_from=None, mtime_to=None,
                   window_limit=TIMELINE_WINDOW_DEFAULT):
    """Build a newest-first timeline from tracked sources and configured work."""
    annotations = load_annotations()
    sources, tasks, _ = load_tracker()
    results = load_work_results()
    work1_results = results.get("work1") or {}
    wiki = load_wiki()
    task_by_source = {
        task.get("source_key"): task for task in tasks.values()
        if task.get("work_name") == "work1"
    }

    rows = []
    for source_key, entry in sources.items():
        if entry.get("missing"):
            continue
        name = entry.get("filename") or os.path.basename(source_key)
        legacy = annotations.get(name, {})
        result = work1_results.get(source_key, {})
        output = result.get("output") or {}
        answer = output.get("answer") or legacy.get("caption") or ""
        task = task_by_source.get(source_key, {})
        result_status = result.get("status")
        if result_status == "error":
            status = "fail"
        elif result_status == "ok":
            status = "ok"
        elif task.get("worker_started_at"):
            status = "pending"
        else:
            status = "none"
        duration = _duration_seconds(result)
        tags = legacy.get("tags") or []
        ocr = legacy.get("OCR_text") or []
        mtime_iso = entry.get("modified_at") or legacy.get("mtime_iso") or ""
        ocr_trunc, truncated = _truncate_ocr(ocr)
        rows.append({
            "source_key": source_key,
            "filename": name,
            "mtime_iso": mtime_iso,
            "mtime_epoch": _iso_to_epoch(mtime_iso),
            "status": status,
            "quality": legacy.get("quality_score"),
            "answer": answer,
            "caption": answer,
            "tags": tags,
            "ocr_text": ocr_trunc,
            "ocr_truncated": truncated,
            "entities": legacy.get("entities") or [],
            "telem_latency_s": duration,
            "telem_status": result_status,
            "telem_timestamp": result.get("finished_at"),
            "telem_error": result.get("error"),
            "in_wiki": name in wiki,
            "has_thumb": _thumb_path_for(name) is not None,
            "original_path": source_key,
        })

    rows.sort(key=lambda row: row["mtime_epoch"], reverse=True)
    window_limit = window_limit if window_limit in TIMELINE_WINDOWS else TIMELINE_WINDOW_DEFAULT
    rows = rows[:window_limit]
    domain_min, domain_max, buckets = _timeline_histogram(
        [row["mtime_epoch"] for row in rows if row["mtime_epoch"] > 0])
    total_rows = len(rows)

    if mtime_from is not None and mtime_to is not None:
        rows = [r for r in rows if mtime_from <= r["mtime_epoch"] <= mtime_to]
    if tag_filter:
        rows = [r for r in rows if tag_filter in r["tags"]]
    if status_filter and status_filter not in ("all", "", None):
        rows = [r for r in rows if r["status"] == status_filter]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["answer"] or "").lower()]

    shown_total = len(rows)
    if limit is not None:
        page = rows[offset:offset + limit]
    else:
        page = rows
    return {
         "rows": page,
         "shown": len(page),
         "shown_total": shown_total,
         "total_rows": total_rows,
         "has_more": (offset + len(page)) < shown_total,
         "mtime_min_epoch": domain_min,
         "mtime_max_epoch": domain_max,
         "mtime_buckets": buckets,
     }


def load_record(filename, source_key=None):
    """Return a full normalized record for a tracked source."""
    sources, _, _ = load_tracker()
    if source_key not in sources:
        source_key = next((key for key, source in sources.items()
                           if source.get("filename") == filename), None)
    if not source_key or sources[source_key].get("missing"):
        return None
    source = sources[source_key]
    legacy = load_annotations().get(source.get("filename"), {})
    result = _work_result(load_work_results(), "work1", source_key)
    output = result.get("output") or {}
    answer = output.get("answer") or legacy.get("caption") or ""
    return {
        "source_key": source_key,
        "filename": source.get("filename") or filename,
        "original_path": source_key,
        "mtime_iso": source.get("modified_at") or legacy.get("mtime_iso") or "",
        "quality_score": legacy.get("quality_score"),
        "answer": answer,
        "caption": answer,
        "tags": legacy.get("tags") or [],
        "entities": legacy.get("entities") or [],
        "ocr_text": legacy.get("OCR_text") or [],
        "ocr_truncated": False,
        "status": result.get("status") or "none",
        "telem_status": result.get("status"),
        "telem_latency_s": _duration_seconds(result),
        "telem_error": result.get("error"),
    }


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type=None):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        if content_type is None:
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(SCRIPT_DIR, "index.html"),
                             "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send_file(os.path.join(SCRIPT_DIR, "app.js"),
                             "application/javascript; charset=utf-8")
            return
        if path == "/style.css":
            self._send_file(os.path.join(SCRIPT_DIR, "style.css"),
                             "text/css; charset=utf-8")
            return

        if path == "/api/overview":
            work_name = qs.get("work", ["work1"])[0]
            self._send_json(build_overview(work_name))
            return

        if path == "/api/tags":
            self._send_json(load_tags_index())
            return

        if path == "/api/telemetry":
            work_name = qs.get("work", ["work1"])[0]
            self._send_json(load_telemetry(work_name))
            return

        if path == "/api/timeline":
            limit = None
            if qs.get("limit", [None])[0]:
                try:
                    limit = int(qs["limit"][0])
                except ValueError:
                    limit = None
            offset = 0
            if qs.get("offset", [None])[0]:
                try:
                    offset = int(qs["offset"][0])
                except ValueError:
                    offset = 0
            status_filter = qs.get("status", [None])[0]
            tag_filter = qs.get("tag", [None])[0]
            query = qs.get("q", [None])[0]
            try:
                window_limit = int(qs.get("window", [TIMELINE_WINDOW_DEFAULT])[0])
            except (TypeError, ValueError):
                window_limit = TIMELINE_WINDOW_DEFAULT
            if window_limit not in TIMELINE_WINDOWS:
                window_limit = TIMELINE_WINDOW_DEFAULT
            mtime_from = _query_float(qs, "mtime_from")
            mtime_to = _query_float(qs, "mtime_to")
            if (mtime_from is None) != (mtime_to is None):
                mtime_from = mtime_to = None
            elif mtime_from is not None and mtime_from > mtime_to:
                mtime_from, mtime_to = mtime_to, mtime_from
            self._send_json(build_timeline(limit=limit, offset=offset,
                                      status_filter=status_filter,
                                      tag_filter=tag_filter, query=query,
                                      mtime_from=mtime_from, mtime_to=mtime_to,
                                      window_limit=window_limit))
            return

        if path == "/api/record":
            filename = qs.get("filename", [None])[0]
            source_key = qs.get("source_key", [None])[0]
            if not filename:
                self._send_json({"error": "filename required"}, 400)
                return
            rec = load_record(unquote(filename), unquote(source_key) if source_key else None)
            if rec is None:
                self._send_json({"error": "not found"}, 404)
            else:
                self._send_json(rec)
            return

        if path.startswith("/thumb/"):
            filename = unquote(path[len("/thumb/"):])
            source_key = qs.get("source_key", [None])[0]
            serve_original = qs.get("original", [None])[0] == "1"
            if serve_original:
                orig = _find_original(filename, unquote(source_key) if source_key else None)
                if orig is None:
                    self._send_json({"error": "original not found"}, 404)
                else:
                    self._send_file(orig, mimetypes.guess_type(orig)[0])
            else:
                thumb = _thumb_path_for(filename)
                if thumb is None:
                    self._send_json({"error": "thumb not generated"}, 404)
                else:
                    self._send_file(thumb, "image/jpeg")
            return

        self._send_json({"error": "unknown route"}, 404)


def main():
    global CURRENT_ENV, TRACKER_PATH, ANNOT_PATH, WIKI_PATH, TAGS_PATH, THUMB_DIR, ENV_CONFIG
    parser = argparse.ArgumentParser(description="Screenshot KB WebUI server")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        help="environment name; omit for the default (.workspace/). "
                              "Unknown names are refused — list via "
                              "'environment_admin.sh init' or backend.py auto-create.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true",
                        help="open the UI in the default browser")
    args = parser.parse_args()
    try:
        CURRENT_ENV, config = config_loader.resolve_environment(
             args.env, auto_bootstrap=False)
        ENV_CONFIG = config
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    TRACKER_PATH = str(config["tracker_path"])
    ANNOT_PATH = str(config["annotations_path"])
    WIKI_PATH = str(config["exports_dir"] / "wiki.ndjson")
    TAGS_PATH = str(config["exports_dir"] / "tags_index.json")
    THUMB_DIR = str(config["thumbnails_dir"])

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, args.port)
    print("WebUI running at %s" % url, flush=True)
    print("Serving from %s" % ROOT, flush=True)
    print("Sources:", flush=True)
    print("  tracker=%s" % TRACKER_PATH, flush=True)
    print("  annotations=%s" % ANNOT_PATH, flush=True)
    print("  wiki=%s" % WIKI_PATH, flush=True)
    print("  tags=%s" % TAGS_PATH, flush=True)
    print("  thumbs=%s" % THUMB_DIR, flush=True)
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
