"""io_util.py -- the one atomic-write helper for the whole pipeline.

Writing a JSON/JSONL artifact the safe way means: write to a *unique* temp file
in the target's directory, close it, then ``os.replace`` it over the target.
``os.replace`` is atomic on POSIX and Windows, so a reader never observes a
half-written or truncated artifact, and a crash mid-write leaves the previous
version (or nothing) intact -- never a corrupted one.

Two of the pipeline's writers used to do this by hand with a *fixed*
``<target>.tmp`` name. That is not just duplicated -- it is latent-buggy: two
concurrent writers to the same target would race on that one temp file and could
clobber or truncate each other before the replace. This module makes the temp
name unique (``tempfile.mkstemp`` in the target's directory) so the race cannot
happen, and collapses the five hand-rolled copies --
``tracker.save_tracker``, ``config_loader._write_config``,
``work5.write_result``, ``work6.write_result``, and ``work7``'s
``atomic_write_json``/``atomic_write_jsonl`` -- into one implementation.

Stdlib-only and Python 3.11-safe.
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path


def atomic_write(path, data):
    """Atomically write ``data`` (a ``str``) to ``path``.

    ``path`` may be a ``str`` or a ``pathlib.Path``; its parent directory is
    created if missing. The text is written to a unique ``tempfile.mkstemp``
    file *in the target's directory* (so the replace stays on one filesystem),
    then ``os.replace``d over the target. On any error the temp file is removed,
    so a failed write leaves no stray artifact behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(data)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return str(path)


def atomic_write_json(path, obj):
    """Atomically write a single JSON object to ``path`` (indented, + newline).

    The one implementation for ``tracker.save_tracker``,
    ``config_loader._write_config`` and ``work7.atomic_write_json``.
    """
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def atomic_write_jsonl(path, records):
    """Atomically write one JSON object per line to ``path``.

    ``records`` is any iterable of JSON-serialisable objects; each becomes one
    ``\\n``-terminated line, in iteration order. This is the one implementation
    for ``work5.write_result``, ``work6.write_result`` and
    ``work7.atomic_write_jsonl``.
    """
    data = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
    atomic_write(path, data)
