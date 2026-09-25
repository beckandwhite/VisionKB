# 001 · HEIC images fail with HTTP 400 at Ollama vision endpoint

**Status:** Resolved (2026-09-25)
**Priority:** Medium  
**Component:** `work_common.py` · `work1.py`  
**Labels:** `bug` `vision` `image-format`

---

## Problem

HEIC images (Apple's default photo format) are sent raw to Ollama's `/api/generate`
vision endpoint, which only accepts JPEG or PNG. Every HEIC file returns:

```
Ollama /api/generate failed: HTTP Error 400: Bad Request
```

Affected files confirmed in `.workspace/work1_generic.jsonl` starting at line 2306,
e.g.:

```
DF73E7FD-E6E5-4746-A5AA-807CA121932B.heic  → HTTP 400
D60E27D6-2128-43A9-844E-B127485B45AA_1_201_a.heic  → HTTP 400
```

The same items appear repeatedly across retries because the retry mechanism
(added alongside this issue) re-queues `status: "error"` tasks — but the root
cause (format rejection) never changes, so they will loop indefinitely without
a fix.

A smaller secondary class of failures was originally attributed to
"Windows-format PNG screenshots with unusual colour profiles" (e.g.
`Screenshot 2024-04-02 131042.png`). On inspection this was **wrong**: those
files are **0 bytes on disk** — failed/incomplete iCloud or export downloads.
Pillow re-encoding cannot rescue them (`Image.open` raises
`UnidentifiedImageError` on empty data); they need an up-front size guard that
fails with a truthful message instead of an opaque HTTP 400.

---

## Root cause

`work_common.vision_request` reads the raw file and base64-encodes it without
any format normalisation:

```python
# work_common.py
with open(source_path, "rb") as source:
    encoded = base64.b64encode(source.read()).decode("ascii")
```

Ollama's vision endpoint has no HEIC decoder and rejects anything it cannot
parse as JPEG or PNG.

---

## Acceptance criteria

- [x] HEIC files are transparently converted to JPEG in memory before the
      base64 payload is built — no files are written to disk.
- [x] The conversion path is exercised only for `.heic` / `.heif` extensions;
      JPEG and PNG pass through unchanged.
- [x] If conversion fails (e.g. `pillow-heif` not installed), the error message
      is clear: `"HEIC conversion failed: install pillow-heif"`.
- [x] Empty (0-byte) sources are rejected up front with a truthful message
      (`"source file is empty (0 bytes) ..."`) instead of an opaque HTTP 400.
      *(Supersedes the original "non-sRGB PNG" acceptance item, which was based
      on a misdiagnosis — the affected files were empty, not mis-profiled.)*
- [x] No new files are created in the source directory or temp dir.
- [x] The failure message is now persisted on the tracker task
      (`last_error` / `last_error_at`) so errors are diagnosable from
      `_tracker.json`, not only the per-work JSONL.

## Implementation notes (as shipped)

- `work_common._load_image_bytes()` handles the empty-file guard and HEIC→JPEG
  transcode; `vision_request()` calls it in place of the raw file read.
- `pillow` + `pillow-heif` recorded in the new `requirements.txt`.
- `tracker.fail_task(task, error=...)` stores the message; `finish_task` clears
  it; `backend.run_task` passes the caught exception through.
- **Not done (deferred):** classifying permanent vs transient errors so
  permanent failures (empty file, undecodable format) stop being re-queued by
  the retry loop. These 14 tasks will still retry until exhausted — tracked
  separately.

---

## Proposed implementation

**Dependency to add** (already in many ML environments):

```
pillow
pillow-heif
```

`pillow-heif` registers itself as a Pillow plugin on import, making
`Image.open()` HEIC-aware.

**Suggested change in `work_common.py`**:

```python
import io
from PIL import Image

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    _HEIF_AVAILABLE = False

_REencode_EXTS = {".heic", ".heif"}
_PROFILE_SAFE_EXTS = {".png"}   # re-encode non-sRGB PNGs too


def _load_image_bytes(source_path: str) -> bytes:
    ext = os.path.splitext(source_path)[1].lower()
    if ext in _REECODE_EXTS:
        if not _HEIF_AVAILABLE:
            raise RuntimeError("HEIC conversion failed: install pillow-heif")
        img = Image.open(source_path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
    if ext in _PROFILE_SAFE_EXTS:
        img = Image.open(source_path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    with open(source_path, "rb") as fh:
        return fh.read()


def vision_request(source_path, prompt, config):
    raw = _load_image_bytes(source_path)
    encoded = base64.b64encode(raw).decode("ascii")
    response = ollama_post_json(config["ollama_base"], "/api/generate", {
        "model": config["vision_model"],
        "prompt": prompt,
        "stream": False,
        "images": [encoded],
    })
    result = response.get("response", "")
    if not result:
        raise RuntimeError("vision returned no response")
    return result.strip()
```

---

## Out of scope

- Storing the converted JPEG on disk as a cache (acceptable future optimisation,
  but not required here).
- Supporting other exotic formats (WebP, TIFF, BMP) — handle on demand.

---

## References

- `work_common.py` — `vision_request()`
- `recommendedHW.md` — model selection context
- Pillow-HEIF: https://github.com/bigcat88/pillow_heif
