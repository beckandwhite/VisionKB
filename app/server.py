#!/usr/bin/env python3
"""
WebUI backend for the screenshot knowledgebase (stdlib only, Python 3.9-safe).

Serves a single-page viewer over the pipeline artifacts. All source files are
re-parsed *fresh per request* so the UI tracks a live pipeline run without a
restart. Read-only: nothing here is written, never touches the pipeline scripts.

Endpoints:
    GET /                        -> app/index.html
    GET /app.js / /style.css     -> static assets
    GET /api/overview            -> funnel stages + ETA + sparkline + status counts
    GET /api/timeline            -> merged rows (annotations x telemetry x wiki),
                                     newest first, capped with has_more
    GET /api/record?filename=    -> full untruncated record for one row
    GET /api/tags                -> passthrough of exports/tags_index.json
    GET /api/telemetry           -> raw telemetry rows
    GET /thumb/<filename>        -> thumbnail from exports/thumbnails/ or 404

Usage:
    python3 app/server.py
    python3 app/server.py --port 8000 --open
"""

import argparse
import json
import mimetypes
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)

TRACKER_PATH = os.path.join(ROOT, "_tracker.json")
TELEMETRY_PATH = os.path.join(ROOT, "telemetry.log")
ANNOT_PATH = os.path.join(ROOT, "_annotations.jsonl")
WIKI_PATH = os.path.join(ROOT, "exports", "wiki.ndjson")
TAGS_PATH = os.path.join(ROOT, "exports", "tags_index.json")
THUMB_DIR = os.path.join(ROOT, "exports", "thumbnails")

OCR_LINE_MAX = 100
OCR_LINES_MAX = 8
TIMELINE_DEFAULT_LIMIT = 150

STAGE_COLORS = {
    "Scanned":         "#5b8def",
    "Vision attempts": "#8a7bff",
    "Vision ok":       "#3fae6f",
    "Annotated":       "#e0a13c",
    "Wiki-ingested":   "#d1495b",
}


# ---------------------------------------------------------------------------
# Loaders (fresh per request; defensive)
# ---------------------------------------------------------------------------

def load_tracker():
    """Return (total, processed) from _tracker.json, handling both the new
    registry schema and the old flat index schema. Missing/corrupt -> (0, 0)."""
    try:
        with open(TRACKER_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return 0, 0
    if not isinstance(data, dict):
        return 0, 0

    runs = data.get("runs")
    if isinstance(runs, dict):
        total = runs.get("total_files") or 0
        processed = runs.get("processed")
        if processed is None:
            unproc = runs.get("unprocessed")
            total = total or (unproc or 0)
            processed = (total - unproc) if unproc is not None else 0
        return int(total or 0), int(processed or 0)

    total = int(data.get("total_images") or data.get("total") or 0)
    processed = int(data.get("processed_so_far") or data.get("processed") or 0)
    return total, processed


def load_telemetry():
    """Return a list of telemetry records (newest last). Blank/malformed skipped."""
    rows = []
    try:
        with open(TELEMETRY_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass
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
    """Return {filename: record} from exports/wiki.ndjson."""
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
    """Absolute path to exports/thumbnails/<stem>.jpg, or None if absent."""
    if not filename:
        return None
    stem = os.path.splitext(filename)[0]
    path = os.path.join(THUMB_DIR, stem + ".jpg")
    return path if os.path.isfile(path) else None


def build_overview():
    """Funnel stage counts, ETA, sparkline, and status chips."""
    telemetry = load_telemetry()
    annotations = load_annotations()
    wiki = load_wiki()
    total, processed_registry = load_tracker()

    ok_count = sum(1 for r in telemetry if r.get("status") == "ok")
    fail_count = sum(1 for r in telemetry if r.get("status") == "fail")
    attempts = len(telemetry)

    annotated = len(annotations)
    wiki_ingested = len(wiki)

    # Denominator = highest count across every source (the tracker total is
    # normally the max, but this stays correct if another source grows faster).
    total = max(total, processed_registry, attempts, ok_count, annotated,
                wiki_ingested)

    ok_latencies = [r["vision_latency_s"] for r in telemetry
                   if r.get("status") == "ok"
                   and isinstance(r.get("vision_latency_s"), (int, float))]
    avg_latency = (sum(ok_latencies) / len(ok_latencies)) if ok_latencies else 0.0

    # The vision step is the bottleneck: remaining = not-yet-classified images.
    classified = max(ok_count, annotated, wiki_ingested, processed_registry)
    remaining = max(total - classified, 0)
    eta_seconds = remaining * avg_latency
    eta_human = _human_duration(eta_seconds) if remaining else "0m"
    projected_finish = (
        (datetime.now(tz=timezone.utc) + timedelta(seconds=eta_seconds)).isoformat()
        if remaining else "")

    def pct(count):
        return round(count / total * 100, 3) if total else 0.0

    stages = [
        {"name": "Scanned", "count": total, "pct": pct(total),
         "color": STAGE_COLORS["Scanned"]},
        {"name": "Vision attempts", "count": attempts, "pct": pct(attempts),
         "color": STAGE_COLORS["Vision attempts"]},
        {"name": "Vision ok", "count": ok_count, "pct": pct(ok_count),
         "color": STAGE_COLORS["Vision ok"]},
        {"name": "Annotated", "count": annotated, "pct": pct(annotated),
         "color": STAGE_COLORS["Annotated"]},
        {"name": "Wiki-ingested", "count": wiki_ingested, "pct": pct(wiki_ingested),
         "color": STAGE_COLORS["Wiki-ingested"]},
    ]

    pending = remaining
    sparkline = []
    for r in telemetry:
        lat = r.get("vision_latency_s")
        if lat is not None:
            sparkline.append({
                "latency_s": round(float(lat), 1),
                "status": r.get("status", "?"),
                "filename": r.get("filename", ""),
                "timestamp": r.get("timestamp", ""),
            })

    return {
        "total": total,
        "stages": stages,
        "avg_latency_s": round(avg_latency, 2),
        "remaining": remaining,
        "eta_seconds": int(eta_seconds),
        "eta_human": eta_human,
        "projected_finish_iso": projected_finish,
        "sparkline": sparkline,
        "status_counts": {
            "ok": ok_count,
            "fail": fail_count,
            "pending": pending,
        },
    }


def build_timeline(limit=None, offset=0, status_filter=None, tag_filter=None,
                  query=None):
    """Merge annotations (detail) + telemetry (status/latency) + wiki (flag),
    newest first, with optional filters and pagination."""
    annotations = load_annotations()
    telemetry = load_telemetry()
    wiki = load_wiki()

    telem_by_name = {}
    for r in telemetry:
        telem_by_name.setdefault(r.get("filename"), []).append(r)

    rows = []
    for name, rec in annotations.items():
        telem = telem_by_name.get(name)
        telem_status = telem_latency = telem_ts = None
        if telem:
            last = telem[-1]
            telem_status = last.get("status")
            telem_latency = last.get("vision_latency_s")
            telem_ts = last.get("timestamp")

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
    """Full untruncated record for one annotation, or None."""
    rec = load_annotations().get(filename)
    if rec is None:
        return None
    rec["ocr_text"] = rec.get("OCR_text") or []
    rec["ocr_truncated"] = False
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
            thumb = _thumb_path_for(filename)
            if thumb is None:
                self._send_json({"error": "thumb not generated"}, 404)
            else:
                self._send_file(thumb, "image/jpeg")
            return

        self._send_json({"error": "unknown route"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Screenshot KB WebUI server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true",
                        help="open the UI in the default browser")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, args.port)
    print("WebUI running at %s" % url, flush=True)
    print("Serving from %s" % ROOT, flush=True)
    print("Sources: trackers=%s telemetry=%s annotations=%s wiki=%s tags=%s"
          % (TRACKER_PATH, TELEMETRY_PATH, ANNOT_PATH, WIKI_PATH, TAGS_PATH),
         flush=True)
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
