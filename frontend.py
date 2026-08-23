#!/usr/bin/env python3
"""
WebUI backend for the screenshot knowledgebase (stdlib only, Python 3.9-safe).

Serves a single-page viewer over the pipeline artifacts. All source files are
re-parsed *fresh per request* so the UI tracks a live pipeline run without a
restart. Read-only: nothing here is written, never touches the pipeline scripts.

Telemetry and per-file progress now live in the shared tracker (_tracker.json);
this server reconstructs telemetry rows from it via tracker.telemetry_from_tracker().

Endpoints:
    GET /                         -> app/index.html
    GET /app.js / /style.css      -> static assets
    GET /api/overview              -> backlog status + ETA (remaining + speed window)
    GET /api/timeline             -> merged rows (annotations x tracker x wiki),
                                     newest first, capped with has_more
    GET /api/record?filename=     -> full untruncated record for one row
    GET /api/tags                 -> passthrough of the environment's tags_index.json
    GET /api/telemetry            -> reconstructed telemetry rows (from the tracker)
    GET /thumb/<file>             -> 320px thumbnail; ?original=1 -> full-res original

Usage:
    python3 app/server.py
    python3 app/server.py --port 8000 --open
"""

import argparse
import json
import mimetypes
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tracker
import config_loader

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

CURRENT_ENV, _CONFIG = config_loader.resolve_environment()
TRACKER_PATH = str(_CONFIG["tracker_path"])
ANNOT_PATH = str(_CONFIG["annotations_path"])
WIKI_PATH = str(_CONFIG["exports_dir"] / "wiki.ndjson")
TAGS_PATH = str(_CONFIG["exports_dir"] / "tags_index.json")
THUMB_DIR = str(_CONFIG["thumbnails_dir"])

OCR_LINE_MAX = 100
OCR_LINES_MAX = 8
TIMELINE_DEFAULT_LIMIT = 150




# ---------------------------------------------------------------------------
# Loaders (fresh per request; defensive)
# ---------------------------------------------------------------------------

def load_tracker():
    """Return (files, runs) from _tracker.json.
    Missing/corrupt/old-schema -> ({}, {}). The registry is read through the
    shared tracker module so the schema stays consistent with the writers."""
    payload = tracker.load_registry(TRACKER_PATH)
    return payload.get("files", {}), payload.get("runs", {})


def load_telemetry():
    """Reconstruct telemetry rows from the tracker (newest last).

    Each processed file yields one row: {timestamp, filename, vision_latency_s,
    tags_count, embedding_dims, status, error}. Backed by the shared tracker.
    """
    files, _ = load_tracker()
    return tracker.telemetry_from_tracker(files)


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


def _find_original(filename):
    """Absolute path to the full-res original for a thumbnail filename, or None.

    Used by /thumb/<file>?original=1 so a thumbnail can be clicked through to
    its source image. Looked up via the annotation record's "filepath"."""
    if not filename:
        return None
    rec = load_annotations().get(filename)
    if rec is None:
        return None
    path = rec.get("filepath")
    if path and os.path.isfile(path):
        return path
    return None


def build_overview():
    """Backlog status + ETA from the tracker.

    Reports how many pictures are still unprocessed and the estimated time
    left, derived from the speed of the most recently processed files.

     `processed` mirrors the pipeline's own "done" predicate (classify_images):
    a file counts as handled when it has a finished_at or processed_at stamp,
    so the backlog reflects the vision queue regardless of KB-layer stages.
    """
    files, runs = load_tracker()
    telemetry = tracker.telemetry_from_tracker(files)

    total = len(files)
    processed = sum(
        1 for e in files.values()
        if e.get("finished_at") or e.get("processed_at")
    )
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


def build_timeline(limit=None, offset=0, status_filter=None, tag_filter=None,
                   query=None):
    """Merge annotations (detail) + tracker telemetry + wiki (flag), newest
    first, with optional filters and pagination."""
    annotations = load_annotations()
    files, runs = load_tracker()
    wiki = load_wiki()

    telem_by_name = {}
    for r in tracker.telemetry_from_tracker(files):
        nm = r.get("filename")
        if nm:
            telem_by_name.setdefault(nm, []).append(r)

    rows = []
    for name, rec in annotations.items():
        telem = telem_by_name.get(name)
        telem_status = telem_latency = telem_ts = telem_error = None
        if telem:
            last = telem[-1]
            telem_status = last.get("status")
            telem_latency = last.get("vision_latency_s")
            telem_ts = last.get("timestamp")
            telem_error = last.get("error")

        tags = rec.get("tags") or []
        ocr = rec.get("OCR_text") or []
        quality = rec.get("quality_score")

        status = telem_status
        if status is None:
            status = "ok" if (rec.get("caption") or tags or ocr) else "none"

        ocr_trunc, truncated = _truncate_ocr(ocr)
        rows.append({
             "filename": name,
             "mtime_iso": rec.get("mtime_iso") or "",
             "mtime_epoch": _iso_to_epoch(rec.get("mtime_iso")),
             "status": status,
             "quality": quality,
             "caption": rec.get("caption") or "",
             "tags": tags,
             "ocr_text": ocr_trunc,
             "ocr_truncated": truncated,
             "entities": rec.get("entities") or [],
             "telem_latency_s": telem_latency,
             "telem_status": telem_status,
             "telem_timestamp": telem_ts,
             "telem_error": telem_error,
             "in_wiki": name in wiki,
             "has_thumb": _thumb_path_for(name) is not None,
             "original_path": rec.get("filepath") or "",
         })

    rows.sort(key=lambda r: r["mtime_epoch"], reverse=True)
    total_rows = len(rows)

    if tag_filter:
        rows = [r for r in rows if tag_filter in r["tags"]]
    if status_filter and status_filter not in ("all", "", None):
        rows = [r for r in rows if r["status"] == status_filter]
    if query:
        q = query.lower()
        rows = [r for r in rows
                if q in (r["caption"] or "").lower()
                or any(q in (t or "").lower() for t in r["tags"])
                or any(q in (o or "").lower() for o in r["ocr_text"])
                or any(q in (e or "").lower() for e in r["entities"])]

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
     }


def load_record(filename):
    """Full untruncated record for one annotation, plus tracker telemetry, or None."""
    rec = load_annotations().get(filename)
    if rec is None:
        return None
    rec["ocr_text"] = rec.get("OCR_text") or []
    rec["ocr_truncated"] = False
    files, runs = load_tracker()
    for r in tracker.telemetry_from_tracker(files):
        if r.get("filename") == filename:
            rec["telem_status"] = r.get("status")
            rec["telem_latency_s"] = r.get("vision_latency_s")
            rec["telem_error"] = r.get("error")
    return rec


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
            self._send_json(build_overview())
            return

        if path == "/api/tags":
            self._send_json(load_tags_index())
            return

        if path == "/api/telemetry":
            self._send_json(load_telemetry())
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
            self._send_json(build_timeline(limit=limit, offset=offset,
                                      status_filter=status_filter,
                                      tag_filter=tag_filter, query=query))
            return

        if path == "/api/record":
            filename = qs.get("filename", [None])[0]
            if not filename:
                self._send_json({"error": "filename required"}, 400)
                return
            rec = load_record(unquote(filename))
            if rec is None:
                self._send_json({"error": "not found"}, 404)
            else:
                self._send_json(rec)
            return

        if path.startswith("/thumb/"):
            filename = unquote(path[len("/thumb/"):])
            serve_original = qs.get("original", [None])[0] == "1"
            if serve_original:
                orig = _find_original(filename)
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
    global CURRENT_ENV, TRACKER_PATH, ANNOT_PATH, WIKI_PATH, TAGS_PATH, THUMB_DIR
    parser = argparse.ArgumentParser(description="Screenshot KB WebUI server")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        choices=config_loader.available_environments())
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true",
                        help="open the UI in the default browser")
    args = parser.parse_args()
    CURRENT_ENV, config = config_loader.resolve_environment(args.env)
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
