#!/usr/bin/env python3
"""Unified screenshot classifier and knowledgebase pipeline."""

import argparse
import fcntl
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import classify_images as classifier
import build_kb
import config_loader
import tracker


LOCK_NAME = ".pipeline.lock"


def configure_modules(config):
    """Apply one resolved environment to the legacy implementation modules."""
    classifier.OLLAMA_BASE = config["ollama_base"]
    classifier.VISION_MODEL = config["vision_model"]
    classifier.EMBED_MODEL = config["embed_model"]
    classifier.IMAGE_EXTS = set(config["supported_images"])
    classifier.SAVE_EVERY = config["save_every"]
    classifier.MAX_DIM = config["max_dim"]
    classifier.TEMP_DIR = config["temp_dir"]
    classifier.TAG_LIST = classifier.tag_list_for_config(config)

    build_kb.DB_PATH = config["db_path"]
    build_kb.ANNOT_PATH = config["annotations_path"]
    build_kb.TRACKER_PATH = config["tracker_path"]
    build_kb.OUTPUT_DIR = config["exports_dir"]


def acquire_lock(path, wait):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "w", encoding="utf-8")
    flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


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


def save_progress(config, files, count_limit, total, new_count, processed, errors, status):
    runs = tracker.build_summary(
        files, count_limit, total,
        new_this_run=new_count,
        processed_this_run=processed,
        errors_this_run=errors,
        status=status,
    )
    tracker.save_tracker(config["tracker_path"], {"files": files, "runs": runs})


def process_pending(config, files, all_images, new_count, count_limit, deadline, no_thumbs, conn):
    pending = [
        path for path in all_images
        if files.get(tracker.file_key(path), {}).get("finished_at") is None
        and files.get(tracker.file_key(path), {}).get("processed_at") is None
    ]
    if count_limit is not None and count_limit > 0:
        pending = pending[:count_limit]

    existing = build_kb.existing_screens(conn, False)
    prompt = classifier.prompt_text()
    processed = 0
    errors = 0
    thumbnails = 0
    started = time.monotonic()

    with open(config["annotations_path"], "a", encoding="utf-8") as annotations:
        for index, image_path in enumerate(pending, 1):
            if deadline and datetime.now() >= deadline:
                break
            key = tracker.file_key(image_path)
            filename = os.path.basename(image_path)
            files.setdefault(key, tracker.new_entry(filename, None))
            tracker.mark_start(files, key)
            print("[%d/%d of %d] %s" %
                (index, len(pending), len(all_images), filename), flush=True)

            image_started = time.monotonic()
            record, ok, error = classifier.classify_one(image_path, prompt)
            elapsed = round(time.monotonic() - image_started, 3)
            annotations.write(json.dumps(record, ensure_ascii=False) + "\n")
            annotations.flush()
            tracker.mark_finish(
                files, key,
                vision_latency_s=elapsed,
                tags_count=len(record["tags"]),
                embedding_dims=len(record["embedding_vector"]),
                quality_score=record["quality_score"],
                ok=ok,
                error=error,
                finished_at=datetime.now().astimezone().isoformat(),
            )

            sid, changed = build_kb.ingest_one(conn, record, existing)
            if changed:
                existing[record.get("filepath", "")] = (sid, record.get("mtime_iso"))
            tracker.mark_ingested(files, key)
            if not no_thumbs:
                before = files[key].get("thumb_status")
                build_kb.generate_thumbnails({key: record}, files, True)
                if before != "ok" and files[key].get("thumb_status") == "ok":
                    thumbnails += 1

            conn.commit()
            tag_counter = build_kb.rebuild_derived(conn)
            build_kb.write_exports(conn, tag_counter)
            conn.commit()
            processed += 1
            errors += int(bool(error))
            save_progress(config, files, count_limit, len(all_images), new_count,
                          processed, errors, "completed")
            print("   %s | tags=%d (%.3fs)" %
                  ("error" if error else ("ok" if ok else "fail"),
                   len(record["tags"]), elapsed), flush=True)

    status = "deadline-reached" if deadline and datetime.now() >= deadline else "completed"
    save_progress(config, files, count_limit, len(all_images), new_count,
                  processed, errors, status)
    print("Done. %d image(s), %d error(s), %d thumbnail(s) in %.1fs." %
          (processed, errors, thumbnails, time.monotonic() - started), file=sys.stderr)


def rebuild_kb(config, files, force, no_thumbs):
    records = build_kb.load_recs()
    conn = sqlite3.connect(str(config["db_path"]))
    try:
        build_kb.create_schema(conn)
        existing = build_kb.existing_screens(conn, force)
        if force:
            for sid, _mtime in list(existing.values()):
                build_kb.delete_screenshot(conn, sid)
            existing = {}
        for key, record in records.items():
            sid, changed = build_kb.ingest_one(conn, record, existing)
            if changed:
                existing[record.get("filepath", "")] = (sid, record.get("mtime_iso"))
            files[key] = files.get(key) or tracker.new_entry(
                record.get("filename", key), record.get("mtime_iso"))
            tracker.mark_ingested(files, key)
        tag_counter = build_kb.rebuild_derived(conn)
        build_kb.write_exports(conn, tag_counter)
        conn.commit()
        if not no_thumbs:
            build_kb.generate_thumbnails(records, files, True)
        save_progress(config, files, None, len(records), 0, 0, 0, "rebuild-complete")
    finally:
        conn.close()


def run(args):
    _env, config = config_loader.resolve_environment(args.env)
    configure_modules(config)
    lock = acquire_lock(config["env_dir"] / LOCK_NAME, args.wait)
    if lock is None:
        print("Another pipeline run is active for %s." % args.env, file=sys.stderr)
        return 0
    try:
        config["db_path"].parent.mkdir(parents=True, exist_ok=True)
        config["exports_dir"].mkdir(parents=True, exist_ok=True)
        payload = tracker.load_registry(config["tracker_path"])
        files = payload["files"]
        source_dir = args.screenshot_dir or config["source_dir"]
        count_limit = config["processed_limit"] if args.count is None else args.count
        images = classifier.list_images(source_dir)
        new_count, _unprocessed = tracker.reconcile(
            source_dir, set(config["supported_images"]), files)
        tracker.seed_from_annotations(config["annotations_path"], files)

        # Persist the complete discovered list before starting processing.
        tracker.save_tracker(
            config["tracker_path"],
            {"files": files, "runs": tracker.build_summary(
                files, count_limit, len(images), new_this_run=new_count,
                status="reconciled")},
        )

        if args.rebuild_kb:
            rebuild_kb(config, files, args.force, args.no_thumbs)
            return 0

        deadline = resolve_deadline(args.until)
        conn = sqlite3.connect(str(config["db_path"]))
        try:
            build_kb.create_schema(conn)
            process_pending(config, files, images, new_count, count_limit,
                           deadline, args.no_thumbs, conn)
        finally:
            conn.close()
        return 0
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def main():
    environments = config_loader.available_environments()
    print("Available environments: %s" % ", ".join(environments))
    parser = argparse.ArgumentParser(description="Unified screenshot pipeline")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        choices=environments)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--screenshot-dir", default=None)
    parser.add_argument("--no-thumbs", action="store_true")
    parser.add_argument("--rebuild-kb", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--until", metavar="HH:MM")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if args.env == config_loader.DEFAULT_ENV:
        print("Using environment: %s (pass -env ENV to select another)" %
              config_loader.DEFAULT_ENV)
    else:
        print("Using environment: %s" % args.env)
    try:
        raise SystemExit(run(args))
    except (RuntimeError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
