#!/usr/bin/env python3
"""
Screenshot classifier using Ollama vision model + embeddings.

Outputs:
      _annotations.jsonl      (one JSON line per image; appended, never rewritten)
     _tracker.json            (per-file registry + run summary -- the single
                               progress ledger AND telemetry log; saved atomically
                               every SAVE_EVERY files). Each processed file records
                               its analysis lifecycle (started_at / finished_at /
                               vision_latency_s / tags_count / embedding_dims /
                               status) plus any error, so the tracker is the
                               backlog queue + log.

The tracker is a self-maintaining registry, not a bare index. Each run:
    1. reconciles the source folder into the tracker (filename + mtime per file,
       keyed by absolute path), appending any files that are new since the last run;
    2. marks every already-processed file with a finished_at timestamp (progress);
    3. classifies the next `--count` UNPROCESSED files (newest mtime first),
       stamping each one's start, finish and -- on any failure -- the error.
Files already annotated in _annotations.jsonl are auto-marked processed on first
reconcile so they are not reclassified.

Usage:
    python3 classify_images.py --count 50                           # classify next 50 unprocessed
    python3 classify_images.py --screenshot-dir '/path/folder'      # scan a custom folder
    python3 classify_images.py                                       # all remaining, default iCloud folder
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tracker
import config_loader


# ============================================================================
# Constants
# ============================================================================

OLLAMA_BASE = config_loader.CURRENT_CONFIG["ollama_base"]
VISION_MODEL = config_loader.CURRENT_CONFIG["vision_model"]
EMBED_MODEL = config_loader.CURRENT_CONFIG["embed_model"]
IMAGE_EXTS = set(config_loader.CURRENT_CONFIG["supported_images"])
SAVE_EVERY = config_loader.CURRENT_CONFIG["save_every"]
MAX_DIM = config_loader.CURRENT_CONFIG["max_dim"]
TEMP_DIR = config_loader.CURRENT_CONFIG["temp_dir"]


TAG_LIST = []


def tag_list_for_config(config):
    """Return the tag taxonomy supplied by the active environment config."""
    tags = config.get("TAG_LIST", [])
    if isinstance(tags, str):
        tags = tags.split()
    if not isinstance(tags, list):
        raise ValueError("Environment config 'tags' must be a list or string")
    return sorted({str(tag).strip() for tag in tags if str(tag).strip()})


TAG_LIST = tag_list_for_config(config_loader.CURRENT_CONFIG)


def get_tags_message():
    """Return scene tags as a single newline-delimited line for the prompt."""
    return "\n".join(TAG_LIST)


# ============================================================================
# Ollama REST calls (stdlib only, no pip deps)
# ============================================================================

def ollama_post_json(endpoint, payload, timeout_val=120):
    """POST to Ollama and return parsed JSON. Raises RuntimeError."""
    url = f"{OLLAMA_BASE}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_val) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except (urllib.error.URLError, ConnectionError, OSError) as exc:
        raise RuntimeError(f"Ollama {endpoint} failed: {exc}") from exc


def ollama_vision(image_path, prompt_text):
    """Run /api/generate with image. Returns text or None on failure.

    All intermediate/converted images (HEIC->JPEG, oversized resize) are written
    to the system temp dir -- never next to the source -- and removed in a
    finally block so the success path also cleans up.
    """
    path = image_path
    tmp_files = []

    def make_tmp():
        p = os.path.join(TEMP_DIR,
                          "snap_" + uuid.uuid4().hex + ".jpg")
        tmp_files.append(p)
        return p

    if path is None:
        return None

    try:
         # HEIC -> JPEG via sips (stderr suppressed; on failure keep original)
        if image_path.lower().endswith(".heic"):
            tmp = make_tmp()
            ret = subprocess.run(
                 ["/usr/bin/sips", "-s", "format", "jpeg", "-o", tmp, path],
                capture_output=True, timeout=30,
             )
            if ret.returncode == 0 and os.path.isfile(tmp):
                path = tmp

         # Resize oversized images to avoid OOM (stderr suppressed)
        try:
            w_out = subprocess.check_output(
                 ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                stderr=subprocess.DEVNULL,
             ).decode()
            dims = {}
            for line in w_out.splitlines():
                parts = line.rstrip().split(":")
                if len(parts) >= 2:
                    key = parts[-2].strip().lower()
                    val = int(parts[-1].strip())
                    dims[key] = val
            w = dims.get("pixelwidth", 0)
            h = dims.get("pixelheight", 0)
            mx = max(w, h)
            if mx > MAX_DIM:
                factor = MAX_DIM / mx
                nw = int(round(w * factor))
                nh = int(round(h * factor))
                resized = make_tmp()
                ret = subprocess.run(
                     ["/usr/bin/sips", "-Z", str(nw), str(nh),
                     path, "--out", resized],
                    capture_output=True, timeout=30,
                 )
                if ret.returncode == 0 and os.path.isfile(resized):
                    path = resized
        except Exception:
            pass

         # Try up to 3 times (call + 2 retries)
        last_exc = None
        for attempt in range(3):
            try:
                b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
                payload = {
                    "model": VISION_MODEL,
                    "prompt": prompt_text,
                    "stream": False,
                    "images": [b64],
                }
                resp_data = ollama_post_json(
                    "/api/generate", payload, timeout_val=180)
                result = resp_data.get("response", "")
                if result:
                    return result.strip()
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
    finally:
         # Clean up every intermediate, including on the success return path.
        for f in tmp_files:
            try:
                os.unlink(f)
            except OSError:
                pass

    return None


def ollama_embed(text_str):
    """Run /api/embed on nomic-embed-text. Returns 768-dim float list."""
    cleaned = text_str.strip() if text_str else ""
    if not cleaned:
        return [0.0] * 768

    try:
        resp_data = ollama_post_json(
            "/api/embed",
            {"model": EMBED_MODEL, "input": cleaned},
            timeout_val=120,
        )
        # nomic-embed-text returns {embeddings:[[float...]]}   -- nested array
        embeds_list = resp_data.get("embeddings", [])
        if len(embeds_list) > 0 and isinstance(embeds_list[0], list):
            vec = embeds_list[0]
            # Pad or truncate to exactly 768 dims
            if len(vec) < 768:
                vec = vec + [0.0] * (768 - len(vec))
            return vec[:768]
    except Exception:
        pass

    return [0.0] * 768


# ============================================================================
# Image list builder
# ============================================================================

def list_images(directory):
    """Scan directory for image files (no dots), return sorted by mtime desc."""
    files = []
    for entry in os.scandir(directory):
        if not entry.is_file():
            continue
        name_lower = entry.name.lower()
        if name_lower.startswith("."):
            continue
        if tracker.is_temp_artifact(entry.name):
            continue
         # Extract extension without leading dot
        if "." in name_lower:
            ext = name_lower.rsplit(".", 1)[-1]
            if ext in IMAGE_EXTS:
                files.append(entry.path)
        elif name_lower.endswith((".png", ".jpg", ".jpeg", ".heic")):
            # Edge case: no extension char (unlikely but defensive)
            pass
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


# ============================================================================
# Helpers
# ============================================================================

def clean_markdown(text):
    """Strip ```json ... or ``` fences from model output."""
    cleaned = text.strip()
    for fence in ["```json", "```"]:
        if cleaned.startswith(fence):
            cleaned = cleaned[len(fence):]
            break
    while "```" in cleaned:
        idx = cleaned.index("```")
        cleaned = cleaned[idx + 3:]
    return cleaned.strip()


def prompt_text():
    """Build the classification prompt with all scene tags embedded."""
    tags_line = "\n".join(TAG_LIST)
    return (
        "You are a screenshot analysis assistant. Analyze the provided image "
        "carefully and output your response strictly in valid JSON with these keys:\n"
        "\n- tags: an array of scene-tags from THIS EXACT list below. Pick all that apply (at least 1 if anything matches):\n"
        + "     " + tags_line + "\n\n"
        "- OCR_text: literal text visible in the image as an array of strings.\n"
        "- entities: notable named entities visible on screen as an array of strings.\n"
        "- caption: a one-sentence plain-English description of the screenshot.\n"
        "- quality_score: integer 1-5 (1=blurry/unusable, 5=crisp/readable).\n"
        "Return ONLY the JSON object with no markdown fences or extra text."
    )


def classify_one(img_path, prompt_str):
    """Run vision + embedding for one image. Returns (record, ok, error).

    Never raises: any failure in the vision/embedding path is captured in `error`
    so the caller can record it in the tracker and continue. `ok` is False when no
    tags came back (even without a hard error -- a "fail" result).
    """
    bname = os.path.basename(img_path)
    mtime_iso = datetime.fromtimestamp(
        os.path.getmtime(img_path), tz=timezone.utc
    ).isoformat()

    tags_arr     = []
    ocr_arr      = []
    entities_arr = []
    caption_str  = ""
    quality_val  = 0
    emb_vec      = []
    parse_error  = None
    vision_error = None

    try:
        vis_text = ollama_vision(img_path, prompt_str)
        if vis_text is None:
            vision_error = "vision: no result after retries"
        else:
            try:
                parsed = json.loads(clean_markdown(vis_text))
                ta = parsed.get("tags", [])
                if isinstance(ta, list):
                    tags_arr = [str(x) for x in ta]
                oa = parsed.get("OCR_text", [])
                if isinstance(oa, list):
                    ocr_arr = [str(x) for x in oa]
                ea = parsed.get("entities", [])
                if isinstance(ea, list):
                    entities_arr = [str(x) for x in ea]
                caption_str = str(parsed.get("caption", ""))
                quality_val = int(parsed.get("quality_score", 0))
            except (json.JSONDecodeError, ValueError) as exc:
                parse_error = f"parse-error: {exc}"

        # Embedding: always call the model (degrades to zeros on failure)
        embed_source = caption_str or "\n".join(tags_arr) or bname
        emb_vec = ollama_embed(embed_source)
    except Exception as exc:
        vision_error = f"{type(exc).__name__}: {exc}"

    ok = bool(tags_arr)
    err = parse_error or vision_error
    record = {
        "filename":        bname,
        "filepath":        os.path.abspath(img_path),
        "mtime_iso":       mtime_iso,
        "tags":            tags_arr,
        "OCR_text":        ocr_arr,
        "entities":        entities_arr,
        "caption":         caption_str,
        "quality_score":   quality_val,
        "embedding_vector": emb_vec,
    }
    return record, ok, err


# ============================================================================
# Build and process
# ============================================================================

def main(count_limit, screenshot_dir, env="DEV"):
    global OLLAMA_BASE, VISION_MODEL, EMBED_MODEL, IMAGE_EXTS
    global SAVE_EVERY, MAX_DIM, TEMP_DIR, TAG_LIST
    _, config = config_loader.resolve_environment(env)
    OLLAMA_BASE = config["ollama_base"]
    VISION_MODEL = config["vision_model"]
    EMBED_MODEL = config["embed_model"]
    IMAGE_EXTS = set(config["supported_images"])
    SAVE_EVERY = int(config["save_every"])
    MAX_DIM = int(config["max_dim"])
    TEMP_DIR = config["temp_dir"]
    TAG_LIST = tag_list_for_config(config)
    if count_limit is None:
        count_limit = int(config["processed_limit"])
    tracker_path = str(config["tracker_path"])
    annot_path = str(config["annotations_path"])

    # Load the existing registry (empty if absent / old flat-index schema).
    payload = tracker.load_registry(tracker_path)
    files = payload["files"]
    migrated = len(files) == 0 and os.path.exists(tracker_path)

    # Refresh the folder list into the registry: append files new since the
    # last run and refresh mtime on known files. This is the "keep the list
    # current" step; progress is tracked via each file's finished_at.
    screenshot_dir = os.path.expanduser(screenshot_dir or config["source_dir"])
    all_images = list_images(screenshot_dir)
    total_images = len(all_images)
    new_count, unprocessed = tracker.reconcile(screenshot_dir, IMAGE_EXTS, files)

    # Auto-mark already-annotated files as processed so they are not reclassified.
    seeded = tracker.seed_from_annotations(annot_path, files)
    if migrated:
        print(f"_tracker.json was an old index checkpoint; "
              f"rebuilding registry from folder + {len(files)} annotated files.",
              file=sys.stderr)
    print(f"Reconciled {total_images} files: {new_count} new, "
          f"{total_images - new_count} known, {unprocessed} unprocessed.",
          file=sys.stderr)
    if seeded:
        print(f"Seeded {seeded} already-annotated file(s) as processed "
              f"(skipped reclassification).", file=sys.stderr)

    # Select the next N unprocessed files, newest mtime first. all_images is
    # mtime-descending from list_images(); filtering preserves that order. A
    # file is unprocessed until it has a finished_at (or a backfilled processed_at).
    def done(entry):
        return entry.get("finished_at") is not None \
            or entry.get("processed_at") is not None
    pending = [p for p in all_images
               if not done(files.get(tracker.file_key(p), {}))]
    if count_limit > 0:
        batch = pending[:count_limit]
    else:
        batch = list(pending)

    # Per-run summary block; enriched with a status tally by the tracker.
    def run_summary(processed_count, error_count, status):
        return tracker.build_summary(
            files, count_limit, total_images,
            new_this_run=new_count,
            processed_this_run=processed_count,
            errors_this_run=error_count,
            status=status,
        )

    if not batch:
        print("Nothing to do: all files already processed.", file=sys.stderr)
        tracker.save_tracker(tracker_path,
            {"files": files, "runs": run_summary(0, 0, "nothing-to-process")})
        return

    prompt_str = prompt_text()
    processed_count = 0
    error_count = 0
    global_t0 = time.monotonic()

    with open(annot_path, "a", encoding="utf-8") as ann_fh:
        for i, img_path in enumerate(batch):
            key   = tracker.file_key(img_path)
            bname = os.path.basename(img_path)
            # Make sure the scanned path is in the registry even if reconcile
            # skipped it for some reason.
            files.setdefault(key, tracker.new_entry(bname, None))

            print(f"[{i + 1}/{len(batch)}] {bname}", flush=True)

            # Stamp the start; everything below is guarded (classify_one) so an
            # unexpected failure is captured as an error, not a crash.
            t0 = time.monotonic()
            tracker.mark_start(files, key)

            record, ok, err = classify_one(img_path, prompt_str)
            elapsed = round(time.monotonic() - t0, 3)
            status = "error" if err else ("ok" if ok else "fail")

            ann_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            ann_fh.flush()
            processed_count += 1
            if err:
                error_count += 1
                print(f"      {status} | {err} (took {elapsed}s)", flush=True)
            else:
                print(f"   {status} | tags={len(record['tags'])} "
                      f"ocr={len(record['OCR_text'])} "
                       f"emb={len(record['embedding_vector'])} (took {elapsed}s)",
                      flush=True)

            # Record the finish -- lifecycle + telemetry + any error live in the
            # tracker (the single log), not a separate telemetry.log file.
            tracker.mark_finish(
                files, key,
                vision_latency_s=elapsed,
                tags_count=len(record["tags"]),
                embedding_dims=len(record["embedding_vector"]),
                quality_score=record["quality_score"],
                ok=ok,
                error=err,
                finished_at=datetime.now(tz=timezone.utc).isoformat(),
            )

            total_done = i + 1
            if total_done % SAVE_EVERY == 0 or i == len(batch) - 1:
                # Mark processed + checkpoint (resumable on crash / interrupt).
                # finished_at is written the moment a file is done, so an
                # interrupt leaves a clean, accurate registry.
                tracker.save_tracker(tracker_path,
                    {"files": files, "runs": run_summary(
                        processed_count, error_count, "completed")})
                # Brief pause after checkpoint so Ollama can cool down
                if i < len(batch) - 1:
                    time.sleep(0.3)

    overall_s = round(time.monotonic() - global_t0, 3)
    print(f"\nDone. {processed_count}/{len(batch)} in {overall_s}s. "
          f"({error_count} error)", file=sys.stderr)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("-env", default="DEV",
                   choices=config_loader.available_environments(),
                   help="Environment workspace to use (default: DEV)")
    p.add_argument("--count", type=int, default=None,
                   help="Limit to N images (for testing)")
    p.add_argument("--screenshot-dir", default=None,
                   help="Folder to scan for screenshots "
                        "(default: iCloud Screenshots)")
    args = p.parse_args()
    main(args.count, args.screenshot_dir, args.env)
