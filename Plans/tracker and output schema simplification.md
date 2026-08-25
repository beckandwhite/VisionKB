# Tracker And Output Schema Simplification

## Goal

Simplify the tracker and work artifacts around clear ownership. The tracker owns source metadata and worker lifecycle; work outputs contain only the source foreign key and the work output.

This is a greenfield schema change for disposable environments. Existing schema-2 trackers and generated JSONL artifacts are not migrated; regenerate them from the configuration template.

## Target contracts

### Tracker

Schema version 3:

```json
{
  "schema_version": 3,
  "sources": {
    "/absolute/path/image.png": {
      "filename": "image.png",
      "created_at": "...",
      "modified_at": "...",
      "discovered_at": "...",
      "missing": false
    }
  },
  "tasks": {
    "stable-task-id": {
      "source_key": "/absolute/path/image.png",
      "work_name": "work1",
      "input_modified_at": "...",
      "status": "pending",
      "worker_started_at": null,
      "worker_id": null,
      "worker_finished_at": null
    }
  },
  "runs": {}
}
```

The `sources` map key is the authoritative source identity, so source values do not repeat `source_key`. `source_key` remains in tasks because tasks need an explicit foreign key. `work_name` remains in tasks because a source can have multiple independent works.

Task status is tracker-owned: `pending`, `running`, `finished`, or `error`. A source modification resets its tasks to `pending`. Failed tasks retain their error status for observability but remain eligible for retry on a later run.

### Per-source work output

Each JSONL record contains only:

```json
{
  "source_key": "/absolute/path/image.png",
  "output": {"answer": "..."}
}
```

The configured result filename identifies the work, so result records do not contain `work_name`. Filename, source dates, worker identity, lifecycle timestamps, and status are joined from the tracker. On failure, the result keeps the longer error detail inside `output`; the tracker task records the lifecycle error status.

Work 4 remains file-producing: the thumbnail is the output and the tracker task records its lifecycle. Work 5 is also included in the cleanup; its duplicate report retains run/group/hash/count information, while source members reference tracker sources by `source_key` rather than copying tracker metadata.

## Implementation steps

1. Update `tracker.py` to schema version 3, remove nested source `source_key`, and add explicit task status transitions.
2. Simplify result creation in `work_common.py`, `work1.py`, `work2.py`, `work3.py`, and `new_worker_template.py`.
3. Make `backend.py` own task success and failure persistence; failed work must not be marked finished.
4. Normalize source access throughout the queue and workers for the new source shape.
5. Update `frontend.py` to join results, sources, and tasks by `source_key`, with metadata and status coming from the tracker.
6. Normalize `work5.py` duplicate output without copying tracker-owned source metadata.
7. Update README and existing design documents with the schema-3 contracts and no-migration workflow.
8. Add focused temporary-directory fixtures for source serialization, task transitions, mtime invalidation, minimal outputs, failure/retry behavior, Work 4, Work 5, and duplicate basenames.

## Scope boundaries

Do not redesign prompts, model behavior, thumbnail naming, visual WebUI design, or the broader wiki/SQLite pipeline. Do not migrate or clean existing generated artifacts.

## Verification

- Compile all touched Python modules using the repository's Python 3.9-compatible command.
- Assert schema 3 persistence has no nested source `source_key`.
- Assert task status transitions and retry behavior.
- Assert work1/work2/work3 output records contain only `source_key` and `output`.
- Verify Work 4 and Work 5 fixtures.
- Verify frontend joins remain distinct for duplicate basenames.
- Run `git diff --check`.
