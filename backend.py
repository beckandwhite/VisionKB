#!/usr/bin/env python3
"""Generic picture work queue runner.

Each configured per-source work is independent. Analytical works append JSONL
results; file-producing works create their configured files. Dataset-wide
producers, such as work5.py, run separately.
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config_loader
import tracker
import work1
import work2
import work3
import work4

ROOT = Path(__file__).resolve().parent
LOCK_NAME = ".pipeline.lock"
HANDLERS = {"work1": work1.run, "work2": work2.run, "work3": work3.run,
            "work4": work4.run}


def list_images(directory, extensions):
    paths = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isfile(path) or name.startswith("."):
                continue
            if tracker.is_temp_artifact(name):
                continue
            if "." in name and name.lower().rsplit(".", 1)[-1] in extensions:
                paths.append(tracker.file_key(path))
    return sorted(paths, key=lambda path: os.path.getmtime(path), reverse=True)


def acquire_lock(path, wait):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w", encoding="utf-8")
    flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def work_by_name(config, name):
    return next((work for work in config.get("works", [])
                 if work.get("name") == name), None)


def append_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False) + "\n")
        output.flush()


def run_task(source, task, work, config, worker_id):
    tracker.claim_task(task, worker_id)
    started = time.monotonic()
    try:
        if work.get("handler") == "work4":
            success = HANDLERS["work4"](source, config)
            result = None
            if not success:
                raise RuntimeError("thumbnail generation failed")
        else:
            handler = HANDLERS.get(work.get("handler"))
            if handler is None:
                raise RuntimeError("unknown work handler: %s" % work.get("handler"))
            result = handler(source, config)
            result_path = config["env_dir"] / work.get(
                "result_file", "%s.jsonl" % work["name"])
            append_result(result_path, result)
        tracker.finish_task(task, source.get("modified_at"))
        return True, time.monotonic() - started
    except Exception as exc:
        # Failure is intentionally not serialized in the tracker. Keep the
        # task unfinished so a later run can retry it after the worker exits.
        task["worker_started_at"] = None
        task["worker_id"] = None
        task["worker_finished_at"] = None
        print("    %s: %s" % (work["name"], exc), file=sys.stderr)
        return False, time.monotonic() - started


def save_progress(config, sources, tasks, count_limit, total, new_count,
                  processed, errors, status):
    runs = tracker.build_summary(
        sources, tasks, count_limit, total, new_this_run=new_count,
        processed_this_run=processed, errors_this_run=errors, status=status)
    tracker.save_tracker(config["tracker_path"], {
        "sources": sources, "tasks": tasks, "runs": runs})


def run(args):
    _env, config = config_loader.resolve_environment(args.env)
    lock = acquire_lock(config["env_dir"] / LOCK_NAME, args.wait)
    if lock is None:
        print("Another pipeline run is active for %s." % args.env, file=sys.stderr)
        return 0
    try:
        source_dir = os.path.expanduser(args.screenshot_dir or config["source_dir"])
        images = list_images(source_dir, set(config["supported_images"]))
        payload = tracker.load_registry(config["tracker_path"])
        sources, tasks = payload["sources"], payload["tasks"]
        new_count, _total = tracker.reconcile(
            source_dir, set(config["supported_images"]), sources)
        per_source = [work["name"] for work in config["works"]
                      if work.get("enabled", True) and work["scope"] == "per_source"]
        tracker.ensure_tasks(sources, tasks, per_source)
        count_limit = config["processed_limit"] if args.count is None else args.count
        save_progress(config, sources, tasks, count_limit, len(images), new_count,
                      0, 0, "reconciled")
        wanted = tracker.pending_tasks(sources, tasks, per_source, stale_after_s=3600)
        wanted_keys = {tracker.task_id(task["source_key"], task["work_name"])
                       for task in wanted}
        worker_id = "%s:%s" % (os.uname().nodename, os.getpid())
        processed = errors = 0
        deadline = resolve_deadline(args.until)
        for source_key in images:
            if count_limit and processed >= count_limit:
                break
            if deadline and datetime.now() >= deadline:
                break
            source_tasks = [task for task in tasks.values()
                            if task["source_key"] == source_key
                            and tracker.task_id(source_key, task["work_name"]) in wanted_keys]
            for task in source_tasks:
                work = work_by_name(config, task["work_name"])
                ok, _elapsed = run_task(sources[source_key], task, work, config, worker_id)
                processed += 1
                errors += int(not ok)
                save_progress(config, sources, tasks, count_limit, len(images), new_count,
                              processed, errors, "running")
        save_progress(config, sources, tasks, count_limit, len(images), new_count,
                      processed, errors,
                      "deadline-reached" if deadline and datetime.now() >= deadline else "completed")
        print("Done. %d task(s), %d error(s)." % (processed, errors))
        return 0
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def resolve_deadline(value):
    if not value:
        return None
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (ValueError, TypeError):
        raise ValueError("--until must use HH:MM")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("--until must use a valid 24-hour time")
    now = datetime.now()
    deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)
    return deadline


def main():
    parser = argparse.ArgumentParser(description="Generic picture work queue")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        help="environment name; omit for the default (.workspace/). "
                             "An unknown name is auto-created on first run.")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--screenshot-dir", default=None)
    parser.add_argument("--until", metavar="HH:MM")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
