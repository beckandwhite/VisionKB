"""
tracker.py -- the single progress ledger + telemetry log for the pipeline.

The tracker is a self-maintaining registry keyed by a file's absolute path. It
is the authoritative record of:
    * which files exist / are new / are unprocessed   (the backlog / queue),
    * each file's analysis lifecycle (started_at / finished_at / latency /
       tags_count / embedding_dims / status) -- was telemetry.log,
    * any error that broke a file -- error capture,
    * KB-layer progress (ingested_at / thumb_at / thumb_status).

Producer that mutates the registry:
     backend.py          -- reconcile, analysis lifecycle, KB and thumbnail stamps

Consumers:
  frontend.py         -- load_registry + telemetry_from_tracker

Stdlib-only and Python 3.9-safe (no match, no runtime X | Y unions).
"""

import json
import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Temp-artifact guard
# ---------------------------------------------------------------------------

# Names that mark a file as pipeline debris (intermediate/converted images the
# classifier once wrote next to its source), not a real source image. Scanners
# skip these so leaked intermediates can never re-enter the backlog.
TEMP_MARKERS = {".tmpresize", ".tmp.", ".part", ".icloud"}


def is_temp_artifact(name):
    """Return True if `name` looks like a pipeline temp/debris file.

    Guards the source-folder scanners (list_images / reconcile) against ingesting
    intermediate images the vision step once left in the input folder.
    """
    lower = name.lower()
    return any(marker in lower for marker in TEMP_MARKERS)


# ---------------------------------------------------------------------------
# Entry schema
# ---------------------------------------------------------------------------

def new_entry(filename, mtime_iso):
    """Skeleton for a freshly-scanned (unprocessed) source file."""
    return {
        "filename":        filename,
        "mtime_iso":       mtime_iso,
        "started_at":      None,
        "finished_at":     None,
        "status":          "pending",
        "quality_score":   None,
        "vision_latency_s": None,
        "tags_count":      None,
        "embedding_dims":  None,
        "error":           None,
        "ingested_at":     None,
        "thumb_at":        None,
        "thumb_status":    None,
    }


def _ensure_fields(entry):
    """Backfill any missing schema keys on an entry that predates them.

    Preserves fields that are already present so a partial lifecycle (e.g. a
    started_at stamped before a crash, or an ingested_at from a prior build)
    is not clobbered when the file is reconciled again.
    """
    if not isinstance(entry, dict):
        return entry
    for key, default in new_entry(entry.get("filename", ""),
                             entry.get("mtime_iso")).items():
        if key not in entry:
            entry[key] = default
    return entry


def _now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def file_key(path):
    """Stable identity for a source file: its absolute path."""
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# Load / save (atomic)
# ---------------------------------------------------------------------------

def load_registry(path):
    """Load the full tracker payload {"files": {...}, "runs": {...}}.

    Returns an empty {"files": {}, "runs": {}} dict on a missing file, corrupt
    JSON, or the old flat-index schema; the registry is then rebuilt from the
    folder + existing annotations by the caller.
    """
    payload = {"files": {}, "runs": {}}
    if not os.path.exists(path):
        return payload
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        return payload
    files = data.get("files")
    runs = data.get("runs")
    if not isinstance(files, dict):
        return payload
    out_files = {}
    for key, val in files.items():
        out_files[key] = _ensure_fields(val)
    return {"files": out_files, "runs": runs if isinstance(runs, dict) else {}}


def load_files(path):
    """Convenience: just the files map from a tracker path."""
    return load_registry(path)["files"]


def save_tracker(path, payload):
    """Atomically write {"files", "runs"} (write .tmp, then os.replace).

    `path` may be a str or a pathlib.Path.
    """
    if not isinstance(payload, dict):
        return
    path = os.fspath(path)
    out = {"files": payload.get("files", {}), "runs": payload.get("runs", {})}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Per-file lifecycle + telemetry + KB stamps
# ---------------------------------------------------------------------------

def mark_start(files, key, when=None):
    """Stamp when analysis of `key` began. Returns the entry."""
    when = when or _now_iso()
    entry = files.setdefault(key, {})
    entry["started_at"] = when
    if entry.get("status") in (None, "pending"):
        entry["status"] = "processing"
    return entry


def mark_finish(files, key, *, finished_at=None, vision_latency_s=None,
                tags_count=None, embedding_dims=None, quality_score=None,
                ok=None, error=None):
    """Stamp the analysis result of `key` -- the moment it finished.

    `ok` sets status ok/fail when not None; a non-empty `error` forces status
    "error". Returns the entry.
    """
    finished_at = finished_at or _now_iso()
    entry = files.setdefault(key, {})
    entry["finished_at"]      = finished_at
    entry["vision_latency_s"] = vision_latency_s
    entry["tags_count"]       = tags_count
    entry["embedding_dims"]   = embedding_dims
    if quality_score is not None:
        entry["quality_score"] = quality_score
    if error:
        entry["error"] = error
        entry["status"] = "error"
    elif ok is not None:
        entry["error"] = None if ok else (entry.get("error") or None)
        entry["status"] = "ok" if ok else "fail"
    return entry


def mark_backfilled(files, key, quality_score=None, when=None):
    """Mark a file already present in _annotations.jsonl as processed."""
    now = when or _now_iso()
    entry = files.setdefault(key, {})
    existing = entry.get("processed_at") or entry.get("finished_at")
    entry["processed_at"] = existing or now
    entry["finished_at"]  = entry.get("finished_at") or now
    if quality_score is not None:
        entry["quality_score"] = quality_score
    entry["status"] = "backfilled"
    return entry


def mark_ingested(files, key, when=None):
    """Stamp that backend.py finished ingesting this row into the DB."""
    entry = files.setdefault(key, {})
    entry["ingested_at"] = when or _now_iso()
    return entry


def mark_thumbnail(files, key, at, status):
    """Record a thumbnail outcome: status in {"ok", "fail"} plus when."""
    entry = files.setdefault(key, {})
    entry["thumb_at"]     = at
    entry["thumb_status"] = status
    return entry


def is_ingested(entry, mtime_iso):
    """True if an entry was ingested at or after its current mtime.

    A source file whose mtime is newer than its ingested_at needs re-ingest;
    an unchanged file is skipped.
    """
    ingested = entry.get("ingested_at")
    if not ingested:
        return False
    cur = entry.get("mtime_iso")
    if not cur:
        return True
    try:
        return datetime.fromisoformat(ingested) >= datetime.fromisoformat(cur)
    except (ValueError, TypeError):
        return True


def thumb_done(entry, dest_path):
    """True if this file's thumbnail is already on disk or recorded ok."""
    if entry.get("thumb_status") == "ok":
        return True
    if dest_path and os.path.isfile(dest_path):
        return True
    return False


# ---------------------------------------------------------------------------
# Reconcile + seed (folder <-> registry)
# ---------------------------------------------------------------------------

def reconcile(directory, img_exts, files):
    """Upsert every image in `directory` into the `files` map.

    Appends new files (status pending, processed_at null); refreshes mtime_iso
    on already-known files without clobbering their lifecycle. Returns
    (new_count, unprocessed_count).
    """
    all_files = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            name_lower = name.lower()
            if name_lower.startswith("."):
                continue
            if is_temp_artifact(name):
                continue
            if "." in name_lower:
                ext = name_lower.rsplit(".", 1)[-1]
                if ext in img_exts:
                    all_files.append(path)
    all_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    new_count = 0
    for path in all_files:
        key = os.path.abspath(path)
        mtime_iso = datetime.fromtimestamp(
            os.path.getmtime(path), tz=timezone.utc).isoformat()
        rec = files.get(key)
        if rec is None:
            files[key] = new_entry(os.path.basename(path), mtime_iso)
            new_count += 1
        else:
            rec["mtime_iso"] = mtime_iso
            _ensure_fields(rec)
    unprocessed = sum(1 for r in files.values()
                      if r.get("processed_at") is None
                      and r.get("finished_at") is None)
    return new_count, unprocessed


def seed_from_annotations(annot_path, files):
    """Mark files already present in _annotations.jsonl as processed.

    Idempotent: only fills finished_at/processed_at for entries still pending.
    Returns the number of files newly seeded (so they are not reclassified).
    """
    if not os.path.exists(annot_path):
        return 0
    now = datetime.now(tz=timezone.utc).isoformat()
    seeded = 0
    with open(annot_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = rec.get("filepath") or rec.get("filename", "")
            key = os.path.abspath(src)
            entry = files.get(key)
            if entry is None:
                continue
            if entry.get("processed_at") is not None:
                continue
            if entry.get("finished_at") is not None:
                entry["processed_at"] = entry["finished_at"]
                continue
            mark_backfilled(files, key,
                            quality_score=rec.get("quality_score"),
                            when=now)
            seeded += 1
    return seeded


# ---------------------------------------------------------------------------
# Summary + telemetry reconstruction
# ---------------------------------------------------------------------------

def tally(files):
    """Count files by status/lifecycle for the run summary."""
    ok = fail = error = pending = ingested = thumbs = 0
    for entry in files.values():
        status = entry.get("status")
        if status == "ok":
            ok += 1
        elif status == "fail":
            fail += 1
        elif status == "error":
            error += 1
        if entry.get("processed_at") is None and entry.get("finished_at") is None:
            pending += 1
        if entry.get("ingested_at"):
            ingested += 1
        if entry.get("thumb_status") == "ok":
            thumbs += 1
    return {"ok": ok, "fail": fail, "error": error, "pending": pending,
            "ingested": ingested, "thumbnails": thumbs}


def build_summary(files, count_param, total, *, new_this_run=0,
                  processed_this_run=0, errors_this_run=0, status=""):
    """Build the per-run `runs` summary block, enriched with a status tally."""
    counts = tally(files)
    return {
        "last_run_at":        _now_iso(),
        "last_count_param":   count_param,
        "total_files":        total,
        "processed":          len(files) - counts["pending"],
        "unprocessed":        counts["pending"],
        "new_this_run":       new_this_run,
        "processed_this_run": processed_this_run,
        "errors_this_run":    errors_this_run,
        "status":             status,
        "ok":                 counts["ok"],
        "fail":               counts["fail"],
        "error":              counts["error"],
        "ingested":           counts["ingested"],
        "thumbnails":         counts["thumbnails"],
    }


def telemetry_from_tracker(files):
    """Reconstruct the old telemetry.log rows from the tracker (newest last).

    Each processed file yields one row:
    {timestamp, filename, vision_latency_s, tags_count, embedding_dims,
    status, error}. Files without finished_at are skipped.
    """
    rows = []
    for entry in files.values():
        finished = entry.get("finished_at")
        if not finished:
            continue
        rows.append({
            "timestamp":        finished,
            "filename":         entry.get("filename"),
            "vision_latency_s": entry.get("vision_latency_s"),
            "tags_count":       entry.get("tags_count"),
            "embedding_dims":   entry.get("embedding_dims"),
            "status":           entry.get("status"),
            "error":            entry.get("error"),
        })
    rows.sort(key=lambda r: r.get("timestamp") or "")
    return rows

