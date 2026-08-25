# Generic Tracker Implementation Transcript

**Date:** 2026-08-24
**Repository:** VisionKB

## Goal

Rework the picture tracker from a fixed screenshot workflow into a generic queue of independent work items. A source picture can be processed by multiple works, with each work storing its own result independently.

`backend_generic.py` was initially out of scope. It was later updated only so its vision prompt matches Work 1 exactly; its legacy implementation remains otherwise unchanged.

## Decisions

- Queue identity is one task per `(source, work)` pair.
- Source identity is the canonical absolute path.
- Basename is display data only and is not a safe foreign key because names can collide.
- Source metadata records creation time, modification time, and discovery time.
- A changed source modification time makes the relevant work tasks eligible again.
- Work results do not belong in the tracker.
- Analytical work results are stored in separate JSONL files.
- File-producing work results are stored as files.
- The tracker stores only worker lifecycle telemetry.
- Dataset-wide operations are separate producers rather than one task per image.
- Existing DEV generated artifacts and the interrupted DEV run may be cleaned up manually.

## Tracker schema

The tracker uses schema version 3:

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
    "sha256(source_key + work_name)": {
      "source_key": "/absolute/path/image.png",
      "work_name": "work1",
      "input_modified_at": "...",
      "status": "pending",
      "worker_started_at": "...",
      "worker_id": "...",
      "worker_finished_at": "..."
    }
  },
  "runs": {}
}
```

The source map key is the authoritative `source_key`. Task status is tracker-owned
and may be `pending`, `running`, `finished`, or `error`. Work-specific output is
not stored in the tracker.

## Work layout

### Work 1: generic vision query

File: `work1.py`

Prompt:

> What is on this picture? Describe the important visible content.

The result is written to `work1.jsonl`. Each result contains only the source
foreign key and output answer. Metadata and lifecycle are joined from the tracker.

### Work 2: OCR

File: `work2.py`

The default prompt asks the vision model to extract all visible text and return JSON containing a `text` array. Results are written to `work2.jsonl`.

### Work 3: classifier

File: `work3.py`

The default prompt asks the vision model to classify the image and return JSON containing a class and confidence. Results are written to `work3.jsonl`.

### Work 4: thumbnails

File: `work4.py`

Work 4 generates a 320px JPEG in:

```text
.workspace/<ENV>/thumbnails/<source-stem>.jpg
```

There is no Work 4 JSONL result. Completion is represented by the Work 4 task's `worker_finished_at` timestamp. The thumbnail file itself is the result.

### Work 5: similarity and duplicate finding

File: `work5.py`

Work 5 is a dataset-wide producer. It scans all supported pictures, groups exact duplicates using SHA-256, and writes:

```text
.workspace/<ENV>/duplicatefinder.jsonl
```

Each group includes a run ID, generation timestamp, algorithm, hash, source count, duplicate count, and source records with absolute-path foreign keys and filesystem timestamps. A later Python program can consume this artifact.

The public grouping function is `similarity_groups()`.

The artifact remains named `duplicatefinder.jsonl` as the downstream handoff name, while the operation itself is identified as Work 5.

## Configuration

Environment configuration declares works with fields including:

- `name`
- `scope`: `per_source` or `dataset`
- `handler`
- `output`: `jsonl` or `files`
- `result_file` or `output_dir`
- `enabled`

DEV currently declares Work 1, Work 2, Work 3, Work 4, and Work 5.

## Backend

`backend.py` is the generic per-source worker runner. It:

1. Acquires the environment writer lock.
2. Reconciles the source directory.
3. Creates tasks for enabled per-source works.
4. Selects pending or stale tasks.
5. Claims each task with worker ID and start time.
6. Executes the configured handler.
7. Writes JSONL output for analytical works or files for Work 4.
8. Records worker completion in the tracker.
9. Saves tracker progress.

`--count` counts work tasks, not pictures. A picture with four enabled per-source works consumes four task slots.

Work failures are not written as tracker status/error fields. Failed tasks are reset to an unfinished state so a later run can retry them.

## Frontend

The frontend tracker loader was updated to read `sources` and `tasks`. The broader frontend still contains legacy assumptions about annotations and timeline fields and is intentionally left for a separate session.

## backend_generic.py prompt alignment

`backend_generic.py` now imports `work1.DEFAULT_PROMPT` and uses it in its generic vision loop. Its existing raw output format remains unchanged.

The exact shared prompt is:

```text
What is on this picture? Describe the important visible content.
```

## Validation performed

- Python compilation passed for the new tracker, backend, work modules, duplicate producer, frontend, and `backend_generic.py` at the syntax level.
- Generic tracker fixture passed: one source, four tasks, one finished task.
- Work dispatcher fixture passed: Work 1, Work 2, and Work 3 completed independently and wrote separate result files.
- Work 4 fixture passed: an existing thumbnail was preserved, the task completed, and no JSONL was written.
- Work 5 fixture passed: two identical files produced one duplicate group with two sources.
- `git diff --check` passed.

## Known caveat

Pylance reports legacy API diagnostics inside `backend_generic.py` because the tracker was intentionally changed without preserving the old tracker API. The requested prompt alignment is complete, but `backend_generic.py` is not migrated to the new generic queue and should not be treated as the active runner.
