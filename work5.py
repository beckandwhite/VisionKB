#!/usr/bin/env python3
"""Work 5: find exact duplicate pictures and emit a datalake JSONL artifact.

This is intentionally a dataset-wide producer, not a per-source worker task.
It reads the configured picture folder and writes duplicate groups to the
selected environment's ``duplicatefinder.jsonl`` without changing the tracker.
"""

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config_loader
import tracker


def list_images(directory, extensions):
    """Return supported source paths in stable order."""
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
    return sorted(paths)


def hash_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def similarity_groups(paths):
    """Return exact duplicate groups keyed by SHA-256."""
    by_hash = {}
    for path in paths:
        try:
            stat_result = os.stat(path)
            digest = hash_file(path)
        except OSError:
            continue
        by_hash.setdefault(digest, []).append({
            "source_key": path,
            "filename": os.path.basename(path),
            "created_at": datetime.fromtimestamp(
                getattr(stat_result, "st_birthtime", stat_result.st_ctime),
                tz=timezone.utc).isoformat(),
            "modified_at": datetime.fromtimestamp(
                stat_result.st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes": stat_result.st_size,
        })
    return {digest: members for digest, members in by_hash.items()
            if len(members) > 1}


def write_result(path, groups, source_count):
    """Atomically replace the duplicate report with one record per group."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    now = datetime.now(tz=timezone.utc).isoformat()
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for digest, members in sorted(groups.items()):
                output.write(json.dumps({
                    "run_id": run_id,
                    "generated_at": now,
                    "algorithm": "sha256",
                    "sha256": digest,
                    "source_count": source_count,
                    "duplicate_count": len(members),
                    "sources": members,
                }, ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return run_id


def main():
    parser = argparse.ArgumentParser(description="Work 5: find similar pictures")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        help="environment name; omit for the default (.workspace/). "
                              "An unknown name is auto-created on first run.")
    parser.add_argument("--screenshot-dir", default=None)
    parser.add_argument("--output", default=None,
                        help="override the work5 duplicatefinder.jsonl path")
    args = parser.parse_args()

    _env, config = config_loader.resolve_environment(args.env)
    source_dir = args.screenshot_dir or config["source_dir"]
    output = args.output or str(config["env_dir"] / "duplicatefinder.jsonl")
    paths = list_images(source_dir, set(config["supported_images"]))
    groups = similarity_groups(paths)
    run_id = write_result(output, groups, len(paths))
    print("Duplicate scan %s: %d picture(s), %d duplicate group(s), %s" %
          (run_id, len(paths), len(groups), output))


if __name__ == "__main__":
    main()
