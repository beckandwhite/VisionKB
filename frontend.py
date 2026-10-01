#!/usr/bin/env python3
"""
WebUI backend for the screenshot knowledgebase (stdlib only, Python 3.11-safe).

Serves a single-page viewer over the pipeline artifacts. All source files are
re-parsed *fresh per request* so the UI tracks a live pipeline run without a
restart. Primarily read-only; the single write route (POST /api/config) saves
editable config fields to the resolved config.json (localhost-only, no auth;
restart required to apply changes).

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
    GET /api/config               -> redacted active config as JSON object
    POST /api/config              -> validate and save editable config fields
    GET /api/telemetry            -> reconstructed telemetry rows (from the tracker)
    GET /api/logs                  -> error tasks newest-first
                                     {filename, work_name, last_error, last_error_at}
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
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import contextlib

import config_loader
import tracker

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = SCRIPT_DIR

# Resolved in main() via config_loader.resolve_environment(..., auto_bootstrap=False).
# This module is read-only: it never creates a config, so an uninitialised
# environment fails fast with an actionable message instead of a silent copy.
CURRENT_ENV, TRACKER_PATH, CONFIG_PATH = None, None, None
ANNOT_PATH = WIKI_PATH = TAGS_PATH = THUMB_DIR = None
ENV_CONFIG = None

OCR_LINE_MAX = 100
OCR_LINES_MAX = 8
TIMELINE_DEFAULT_LIMIT = 150
TIMELINE_WINDOW_DEFAULT = 50
TIMELINE_WINDOWS = (50, 100, 150, 200)
MAX_POST_BODY = 65536  # 64 KB — ample for a config JSON


# ---------------------------------------------------------------------------
# Per-work metric registry
# Keys are work names; absence means fall back to latency (vision_latency_s).
# Each entry: chart_value(result) -> number, chart_unit: str,
#             overview_extras: bool (compute empty_result_rate + error_rate).
# ---------------------------------------------------------------------------


def _work2_lines(result):
    output = (result or {}).get("output") or {}
    text = output.get("text") or []
    if isinstance(text, list):
        return len(text)
    if isinstance(text, str):
        return len([ln for ln in text.splitlines() if ln.strip()])
    return 0


def _work2_ocr_text(result):
    """Extract OCR lines from a work2 result as a list of strings."""
    output = (result or {}).get("output") or {}
    text = output.get("text")
    if isinstance(text, list):
        return [str(line) for line in text]
    if isinstance(text, str):
        return [ln for ln in text.splitlines() if ln.strip()]
    return []


WORK_METRIC_REGISTRY = {
    "work2": {
        "chart_value": _work2_lines,
        "chart_unit": "lines",
        "overview_extras": True,
    },
}


def list_per_source_works():
    """Return names of enabled per_source works from the active config."""
    if not ENV_CONFIG:
        return []
    return [
        w["name"]
        for w in ENV_CONFIG.get("works", [])
        if w.get("enabled") and w.get("scope") == "per_source"
    ]


def _json_safe(v):
    """Recursively convert non-JSON-serialisable values (Path, etc.) to strings."""
    if isinstance(v, os.PathLike):
        return str(v)
    if isinstance(v, dict):
        return {k: _json_safe(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_json_safe(item) for item in v]
    return v


def _redact_config():
    """Return a JSON-safe, redacted copy of ENV_CONFIG.

    - Keys whose name contains 'key', 'token', or 'secret' are replaced with '***'.
    - TAG_LIST is replaced with a count string to avoid sending thousands of tags.
    - Path objects are serialised as strings.
    """
    if not ENV_CONFIG:
        return {}
    secret_substrings = ("key", "token", "secret")
    out = {}
    for k, v in ENV_CONFIG.items():
        if any(s in k.lower() for s in secret_substrings):
            out[k] = "***"
        elif k == "TAG_LIST":
            tag_count = len(str(v).split()) if v else 0
            out[k] = f"<{tag_count} tags>"
        else:
            out[k] = _json_safe(v)
    return out


# ---------------------------------------------------------------------------
# Loaders (fresh per request; defensive)
# ---------------------------------------------------------------------------


def load_tracker():
    """Return (sources, tasks, runs) from _tracker.json.
    Missing/corrupt/old-schema -> ({}, {}). The registry is read through the
    shared tracker module so the schema stays consistent with the writers."""
    payload = tracker.load_registry(TRACKER_PATH)
    return (payload.get("sources", {}), payload.get("tasks", {}), payload.get("runs", {}))


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


def _display_status(task):
    status = task.get("status")
    if status == "finished":
        return "ok"
    if status == "error":
        return "fail"
    if status == "running":
        return "pending"
    return "none"


def load_telemetry(work_name="work1"):
    """Reconstruct telemetry rows from the tracker (newest last).

    Each processed file yields one row: {timestamp, filename, source_key,
    work_name, vision_latency_s, chart_value, chart_unit, status}.
    chart_value / chart_unit come from WORK_METRIC_REGISTRY when an entry
    exists for work_name (joined with the work's result JSONL on source_key);
    otherwise chart_value == vision_latency_s and chart_unit == "s".
    """
    sources, tasks, _ = load_tracker()
    registry_entry = WORK_METRIC_REGISTRY.get(work_name)
    work_results = {}
    if registry_entry:
        work_results = load_work_results().get(work_name) or {}
    rows = []
    for task in tasks.values():
        if task.get("work_name") != work_name:
            continue
        if task.get("status") != "finished":
            continue
        source_key = task.get("source_key")
        source = sources.get(source_key, {})
        duration = _task_duration_seconds(task)
        if not source or duration is None:
            continue
        if registry_entry:
            result = work_results.get(source_key) or {}
            chart_value = registry_entry["chart_value"](result)
            chart_unit = registry_entry["chart_unit"]
        else:
            chart_value = duration
            chart_unit = "s"
        rows.append(
            {
                "timestamp": task.get("worker_finished_at"),
                "filename": source.get("filename"),
                "source_key": source_key,
                "work_name": work_name,
                "vision_latency_s": duration,
                "chart_value": chart_value,
                "chart_unit": chart_unit,
                "status": "ok",
            }
        )
    rows.sort(key=lambda row: row.get("timestamp") or "")
    return rows


def load_logs():
    """Return error tasks newest-first as [{filename, work_name, last_error, last_error_at}]."""
    sources, tasks, _ = load_tracker()
    rows = []
    for task in tasks.values():
        if task.get("status") != "error":
            continue
        source_key = task.get("source_key")
        source = sources.get(source_key, {})
        rows.append(
            {
                "filename": source.get("filename"),
                "work_name": task.get("work_name"),
                "last_error": task.get("last_error"),
                "last_error_at": task.get("last_error_at"),
            }
        )
    rows.sort(key=lambda r: r.get("last_error_at") or "", reverse=True)
    return rows


def _backup_and_prune(config_path):
    """Copy config_path to a timestamped .bak then prune to the 5 newest backups."""
    path = Path(config_path)
    if not path.is_file():
        return
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    bak = path.parent / (path.name + "." + ts + ".bak")
    with open(path, "rb") as src:
        bak.write_bytes(src.read())
    baks = sorted(path.parent.glob(path.name + ".*.bak"), key=lambda p: p.stat().st_mtime)
    for old_bak in baks[:-5]:
        old_bak.unlink()


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
                name = rec.get("filename") or "unknown-{}".format(rec.get("sid", ""))
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
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


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
    key = source_key if (source_key and source_key in sources) else None
    if key is None:
        key = next((k for k in sources if sources[k].get("filename") == filename), None)
    if key and os.path.isfile(key):
        return key
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

    active_sources = {key for key, source in sources.items() if not source.get("missing")}
    total = len(active_sources)
    processed = sum(
        1
        for task in tasks.values()
        if task.get("work_name") == work_name
        and task.get("worker_finished_at")
        and task.get("source_key") in active_sources
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
        (datetime.now(tz=UTC) + timedelta(seconds=eta_seconds)).isoformat()
        if (remaining and has_speed)
        else ""
    )

    extra = {}
    registry_entry = WORK_METRIC_REGISTRY.get(work_name)
    if registry_entry and registry_entry.get("overview_extras"):
        work_results = results.get(work_name) or {}
        chart_fn = registry_entry["chart_value"]
        ok_source_keys = [
            t.get("source_key")
            for t in tasks.values()
            if t.get("work_name") == work_name
            and t.get("status") == "finished"
            and t.get("source_key") in active_sources
        ]
        total_ok = len(ok_source_keys)
        empty = sum(1 for sk in ok_source_keys if chart_fn(work_results.get(sk) or {}) == 0)
        error_count = sum(
            1
            for t in tasks.values()
            if t.get("work_name") == work_name
            and t.get("status") == "error"
            and t.get("source_key") in active_sources
        )
        total_attempted = total_ok + error_count
        extra["empty_result_rate"] = round(empty / total_ok, 3) if total_ok else None
        extra["error_rate"] = round(error_count / total_attempted, 3) if total_attempted else None

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
        **extra,
    }


def _timeline_histogram(values, bucket_count=48):
    """Return a stable modification-time domain and density buckets."""
    valid = sorted(value for value in values if math.isfinite(value))
    if not valid:
        return None, None, []
    minimum, maximum = valid[0], valid[-1]
    if minimum == maximum:
        return minimum, maximum, [{"start": minimum, "end": minimum, "count": len(valid)}]

    width = (maximum - minimum) / bucket_count
    buckets = [
        {"start": minimum + i * width, "end": minimum + (i + 1) * width, "count": 0}
        for i in range(bucket_count)
    ]
    for value in valid:
        index = min(int((value - minimum) / width), bucket_count - 1)
        buckets[index]["count"] += 1
    return minimum, maximum, buckets


def build_timeline(
    limit=None,
    offset=0,
    status_filter=None,
    tag_filter=None,
    query=None,
    mtime_from=None,
    mtime_to=None,
    window_limit=TIMELINE_WINDOW_DEFAULT,
):
    """Build a newest-first timeline from tracked sources and configured work."""
    annotations = load_annotations()
    sources, tasks, _ = load_tracker()
    results = load_work_results()
    work1_results = results.get("work1") or {}
    work2_results = results.get("work2") or {}
    wiki = load_wiki()
    task_by_source = {
        task.get("source_key"): task for task in tasks.values() if task.get("work_name") == "work1"
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
        status = _display_status(task)
        duration = _task_duration_seconds(task)
        tags = legacy.get("tags") or []
        ocr = _work2_ocr_text(work2_results.get(source_key, {})) or legacy.get("OCR_text") or []
        mtime_iso = entry.get("modified_at") or legacy.get("mtime_iso") or ""
        ocr_trunc, truncated = _truncate_ocr(ocr)
        rows.append(
            {
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
                "telem_status": task.get("status"),
                "telem_timestamp": task.get("worker_finished_at"),
                "telem_error": output.get("error"),
                "in_wiki": name in wiki,
                "has_thumb": _thumb_path_for(name) is not None,
                "original_path": source_key,
            }
        )

    rows.sort(key=lambda row: row["mtime_epoch"], reverse=True)
    window_limit = window_limit if window_limit in TIMELINE_WINDOWS else TIMELINE_WINDOW_DEFAULT
    rows = rows[:window_limit]
    domain_min, domain_max, buckets = _timeline_histogram(
        [row["mtime_epoch"] for row in rows if row["mtime_epoch"] > 0]
    )
    total_rows = len(rows)

    if mtime_from is not None and mtime_to is not None:
        rows = [r for r in rows if mtime_from <= r["mtime_epoch"] <= mtime_to]
    if tag_filter:
        rows = [r for r in rows if tag_filter in r["tags"]]
    # Status filter removed\n
    if query:
        q = query.lower()
        rows = [r for r in rows if q in (r["answer"] or "").lower()]

    shown_total = len(rows)
    page = rows[offset : offset + limit] if limit is not None else rows
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
    sources, tasks, _ = load_tracker()
    if source_key not in sources:
        source_key = next(
            (key for key, source in sources.items() if source.get("filename") == filename), None
        )
    if not source_key or sources[source_key].get("missing"):
        return None
    source = sources[source_key]
    task = next(
        (
            item
            for item in tasks.values()
            if item.get("source_key") == source_key and item.get("work_name") == "work1"
        ),
        {},
    )
    legacy = load_annotations().get(source.get("filename"), {})
    all_results = load_work_results()
    result = _work_result(all_results, "work1", source_key)
    output = result.get("output") or {}
    answer = output.get("answer") or legacy.get("caption") or ""
    ocr = (
        _work2_ocr_text(_work_result(all_results, "work2", source_key))
        or legacy.get("OCR_text")
        or []
    )
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
        "ocr_text": ocr,
        "ocr_truncated": False,
        "status": _display_status(task),
        "telem_status": task.get("status"),
        "telem_latency_s": _task_duration_seconds(task),
        "telem_error": (result.get("output") or {}).get("error"),
    }


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

_JS = "application/javascript; charset=utf-8"
# Whitelist of static assets index.html loads, mapped to their content type.
# Every <script>/<link> referenced by index.html must appear here or the tab
# that depends on it renders empty.
STATIC_ASSETS = {
    "/style.css": "text/css; charset=utf-8",
    "/app.js": _JS,
    "/tagforge.js": _JS,
    "/telemetry.js": _JS,
    "/setup.js": _JS,
    "/feedback.js": _JS,
}


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

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/config":
            self._send_json({"error": "unknown route"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            n = 0
        if n > MAX_POST_BODY:
            self._send_json({"error": "body too large"}, 413)
            return
        try:
            body = self.rfile.read(n) if n else b""
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "invalid JSON body"}, 400)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "body must be a JSON object"}, 400)
            return

        ALLOWED = {
            "ollama_base": str,
            "vision_model": str,
            "embed_model": str,
            "max_dim": int,
            "save_every": int,
            "supported_images": list,
            "source_dir": list,
        }
        for key in data:
            if key not in ALLOWED:
                self._send_json({"error": f"unknown field: {key}"}, 400)
                return
            val = data[key]
            exp = ALLOWED[key]
            if isinstance(val, bool) or not isinstance(val, exp):
                self._send_json({"error": f"field {key} must be {exp.__name__}"}, 400)
                return
            if exp is list and not all(isinstance(item, str) for item in val):
                self._send_json({"error": f"field {key} must be a list of strings"}, 400)
                return

        cfg_path = Path(CONFIG_PATH)
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                current = json.load(fh)
        except (OSError, ValueError):
            self._send_json({"error": "could not read current config"}, 500)
            return

        _backup_and_prune(cfg_path)
        current.update(data)
        config_loader._write_config(cfg_path, current)
        self._send_json({"saved": True, "restart_required": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._send_file(os.path.join(SCRIPT_DIR, "index.html"), "text/html; charset=utf-8")
            return
        if path in STATIC_ASSETS:
            self._send_file(os.path.join(SCRIPT_DIR, path.lstrip("/")), STATIC_ASSETS[path])
            return

        if path == "/api/overview":
            work_name = qs.get("work", ["work1"])[0]
            self._send_json(build_overview(work_name))
            return

        if path == "/api/works":
            self._send_json(list_per_source_works())
            return

        if path == "/api/config":
            self._send_json({"environment": CURRENT_ENV, "config": _redact_config()})
            return

        if path == "/api/tags":
            self._send_json(load_tags_index())
            return

        if path == "/api/logs":
            self._send_json(load_logs())
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
            self._send_json(
                build_timeline(
                    limit=limit,
                    offset=offset,
                    status_filter=status_filter,
                    tag_filter=tag_filter,
                    query=query,
                    mtime_from=mtime_from,
                    mtime_to=mtime_to,
                    window_limit=window_limit,
                )
            )
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
            filename = unquote(path[len("/thumb/") :])
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
    global \
        CURRENT_ENV, \
        TRACKER_PATH, \
        CONFIG_PATH, \
        ANNOT_PATH, \
        WIKI_PATH, \
        TAGS_PATH, \
        THUMB_DIR, \
        ENV_CONFIG
    parser = argparse.ArgumentParser(description="Screenshot KB WebUI server")
    parser.add_argument(
        "-env",
        default=config_loader.DEFAULT_ENV,
        help="environment name; omit for the default (.workspace/). "
        "Unknown names are refused — list via "
        "'environment_admin.sh init' or backend.py auto-create.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="open the UI in the default browser")
    args = parser.parse_args()
    try:
        CURRENT_ENV, config = config_loader.resolve_environment(args.env, auto_bootstrap=False)
        ENV_CONFIG = config
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    TRACKER_PATH = str(config["tracker_path"])
    CONFIG_PATH = str(config_loader.config_path_for(CURRENT_ENV))
    ANNOT_PATH = str(config["annotations_path"])
    WIKI_PATH = str(config["exports_dir"] / "wiki.ndjson")
    TAGS_PATH = str(config["exports_dir"] / "tags_index.json")
    THUMB_DIR = str(config["thumbnails_dir"])

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"WebUI running at {url}", flush=True)
    print(f"Serving from {ROOT}", flush=True)
    print("Sources:", flush=True)
    print(f"  tracker={TRACKER_PATH}", flush=True)
    print(f"  annotations={ANNOT_PATH}", flush=True)
    print(f"  wiki={WIKI_PATH}", flush=True)
    print(f"  tags={TAGS_PATH}", flush=True)
    print(f"  thumbs={THUMB_DIR}", flush=True)
    if args.open:
        with contextlib.suppress(Exception):
            webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
