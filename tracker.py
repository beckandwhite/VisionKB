"""
tracker.py -- source manifest and generic work queue.

The tracker is a self-maintaining registry keyed by a file's absolute path. It
is the authoritative record of:
    * which files exist / are new / are unprocessed   (the backlog / queue),
     * independent worker lifecycle telemetry for configured tasks.

Producers that mutate the registry:
    backend.py          -- reconcile and per-work worker lifecycle

Consumers:
    frontend.py         -- load_registry + telemetry_from_tracker

Stdlib-only and Python 3.9-safe (no match, no runtime X | Y unions).
"""

import json
import os
import hashlib
from datetime import datetime, timezone


SCHEMA_VERSION = 3


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
# Source and task schema
# ---------------------------------------------------------------------------

def _iso_from_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def source_metadata(path, discovered_at=None):
    """Return filesystem metadata for one picture."""
    path = file_key(path)
    stat_result = os.stat(path)
    birth_timestamp = getattr(stat_result, "st_birthtime", None)
    if birth_timestamp is None:
        birth_timestamp = stat_result.st_ctime
    return {
        "filename": os.path.basename(path),
        "created_at": _iso_from_timestamp(birth_timestamp),
        "modified_at": _iso_from_timestamp(stat_result.st_mtime),
        "discovered_at": discovered_at or _now_iso(),
        "missing": False,
    }


def new_source(path, discovered_at=None, metadata=None):
    """Create a source record, optionally using already-read metadata."""
    if metadata is None:
        metadata = source_metadata(path, discovered_at)
    return dict(metadata)


def task_id(source_key, work_name):
    """Return a stable task key for one source and configured work."""
    raw = "%s\0%s" % (file_key(source_key), str(work_name))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_task(source_key, work_name, input_modified_at=None):
    """Create an unclaimed task containing lifecycle data only."""
    return {
        "source_key": source_key,
        "work_name": work_name,
        "input_modified_at": input_modified_at,
        "status": "pending",
        "worker_started_at": None,
        "worker_id": None,
        "worker_finished_at": None,
    }


def _now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def file_key(path):
    """Stable identity for a source file: its absolute path."""
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# Load / save (atomic)
# ---------------------------------------------------------------------------

def load_registry(path):
    """Load the versioned generic tracker payload.

    Invalid, missing, or legacy payloads return an empty schema-3 registry.
    """
    payload = {"schema_version": SCHEMA_VERSION, "sources": {},
               "tasks": {}, "runs": {}}
    if not os.path.exists(path):
        return payload
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        return payload
    if data.get("schema_version") == SCHEMA_VERSION:
        sources = data.get("sources")
        tasks = data.get("tasks")
        if isinstance(sources, dict) and isinstance(tasks, dict):
            return {"schema_version": SCHEMA_VERSION, "sources": sources,
                    "tasks": tasks,
                    "runs": data.get("runs") if isinstance(data.get("runs"), dict) else {}}
    return payload


def save_tracker(path, payload):
    """Atomically write the generic tracker (write .tmp, then os.replace).

    `path` may be a str or a pathlib.Path.
    """
    if not isinstance(payload, dict):
        return
    path = os.fspath(path)
    out = {"schema_version": SCHEMA_VERSION,
           "sources": payload.get("sources", {}),
           "tasks": payload.get("tasks", {}),
           "runs": payload.get("runs", {})}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Generic task lifecycle
# ---------------------------------------------------------------------------

def ensure_tasks(sources, tasks, work_names):
    """Ensure one task exists for each source and per-source work."""
    for source_key, source in sources.items():
        for work_name in work_names:
            key = task_id(source_key, work_name)
            task = tasks.setdefault(key, new_task(
                source_key, work_name, source.get("modified_at")))
            if task.get("input_modified_at") != source.get("modified_at"):
                task["input_modified_at"] = source.get("modified_at")
                task["status"] = "pending"
                task["worker_started_at"] = None
                task["worker_id"] = None
                task["worker_finished_at"] = None
    return tasks


def pending_tasks(sources, tasks, work_names=None, stale_after_s=None, now=None):
    """Return current-version tasks that are unclaimed or stale."""
    now = now or datetime.now(tz=timezone.utc)
    wanted = set(work_names) if work_names is not None else None
    result = []
    for task in tasks.values():
        if wanted is not None and task.get("work_name") not in wanted:
            continue
        source = sources.get(task.get("source_key"))
        if not source or source.get("missing"):
            continue
        if task.get("input_modified_at") != source.get("modified_at"):
            result.append(task)
            continue
        if task.get("status") == "finished":
            continue
        started = task.get("worker_started_at")
        if started and stale_after_s is not None:
            try:
                age = (now - datetime.fromisoformat(started)).total_seconds()
            except (TypeError, ValueError):
                age = stale_after_s
            if age < stale_after_s:
                continue
        result.append(task)
    return result


def claim_task(task, worker_id, when=None):
    """Claim a task; callers must hold the environment writer lock."""
    task["status"] = "running"
    task["worker_started_at"] = when or _now_iso()
    task["worker_id"] = worker_id
    task["worker_finished_at"] = None
    return task


def finish_task(task, input_modified_at, when=None):
    """Record successful worker completion telemetry."""
    task["input_modified_at"] = input_modified_at
    task["status"] = "finished"
    task["worker_finished_at"] = when or _now_iso()
    return task


def fail_task(task, when=None):
    """Record a failed attempt while leaving the task eligible for retry."""
    task["status"] = "error"
    task["worker_finished_at"] = when or _now_iso()
    return task


# ---------------------------------------------------------------------------
# Source reconciliation
# ---------------------------------------------------------------------------

def reconcile(directory, img_exts, files):
    """Upsert every image in ``directory`` into the source map.

    Returns ``(new_count, source_count)``. Existing source records retain their
    identity and discovery time while filesystem timestamps are refreshed.
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
        key = file_key(path)
        metadata = source_metadata(path)
        rec = files.get(key)
        if rec is None:
            files[key] = new_source(key, metadata=metadata)
            new_count += 1
        else:
            discovered_at = rec.get("discovered_at") or metadata["discovered_at"]
            rec.update(metadata)
            rec["discovered_at"] = discovered_at
    return new_count, len(files)


# Summary + telemetry reconstruction
# ---------------------------------------------------------------------------

def tally(sources, tasks):
    """Count generic task lifecycle states for the run summary."""
    finished = sum(1 for task in tasks.values()
                   if task.get("status") == "finished")
    pending = len(tasks) - finished
    by_work = {}
    for task in tasks.values():
        work = task.get("work_name", "unknown")
        counts = by_work.setdefault(work, {"finished": 0, "pending": 0})
        counts["finished" if task.get("status") == "finished" else "pending"] += 1
    return {"sources": len(sources), "tasks": len(tasks), "finished": finished,
            "pending": pending, "by_work": by_work}


def build_summary(sources, tasks, count_param, total, *, new_this_run=0,
                  processed_this_run=0, errors_this_run=0, status=""):
    """Build the per-run `runs` summary block, enriched with a status tally."""
    counts = tally(sources, tasks)
    return {
        "last_run_at":        _now_iso(),
        "last_count_param":   count_param,
        "total_files":        total,
        "processed":          counts["finished"],
        "unprocessed":        counts["pending"],
        "new_this_run":       new_this_run,
        "processed_this_run": processed_this_run,
        "errors_this_run":    errors_this_run,
        "status":             status,
        "task_counts":        counts["by_work"],
    }


def telemetry_from_tracker(tasks, sources=None):
    """Return generic worker lifecycle rows, newest last."""
    sources = sources or {}
    rows = []
    for task in tasks.values():
        if task.get("status") != "finished":
            continue
        finished = task.get("worker_finished_at")
        if not finished:
            continue
        source = sources.get(task.get("source_key"), {})
        rows.append({
            "timestamp":        finished,
            "filename":         source.get("filename"),
            "source_key":       task.get("source_key"),
            "work_name":        task.get("work_name"),
            "worker_id":        task.get("worker_id"),
        })
    rows.sort(key=lambda r: r.get("timestamp") or "")
    return rows

