#!/usr/bin/env python3
"""Lightweight screenshot-to-text pipeline.

Per image it sends one vision request to Ollama and stores the raw response in
annotation2.json:

  reconcile folder -> _tracker.json
  for each unprocessed image:
    Ollama vision (empty system prompt) -> annotation2.json

The legacy KB rebuild is disabled; this runner only writes generic image
descriptions and tracker state.
"""

import argparse
import base64
import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_loader
import tracker


# ============================================================================
# Constants / globals (overridden per environment by configure_globals)
# ============================================================================

OLLAMA_BASE = config_loader.CURRENT_CONFIG["ollama_base"]
VISION_MODEL = config_loader.CURRENT_CONFIG["vision_model"]
IMAGE_EXTS = set(config_loader.CURRENT_CONFIG["supported_images"])
MAX_DIM = config_loader.CURRENT_CONFIG["max_dim"]
TEMP_DIR = config_loader.CURRENT_CONFIG["temp_dir"]

ANNOT2_PATH = config_loader.CURRENT_CONFIG["env_dir"] / "annotation2.json"

LOCK_NAME = ".pipeline.lock"


def eprint(*args):
    print(*args, flush=True, file=sys.stderr)


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


def ollama_vision(image_path, prompt_text, retries=3):
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

        for attempt in range(retries):
            try:
                b64 = base64.b64encode(open(path, "rb").read()).decode("ascii")
                payload = {
                     "model": VISION_MODEL,
                     "system": "",
                     "prompt": prompt_text,
                     "stream": False,
                     "images": [b64],
                  }
                resp_data = ollama_post_json(
                     "/api/generate", payload, timeout_val=180)
                result = resp_data.get("response", "")
                if result:
                    return result.strip()
            except Exception:
                if attempt < retries - 1:
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
        # nomic-embed-text returns {embeddings:[[float...]]}  -- nested array
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
    """Recursively scan directory for image files, sorted by mtime desc."""
    files = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            entry = os.path.join(root, name)
            if not os.path.isfile(entry):
                continue
            name_lower = name.lower()
            if name_lower.startswith("."):
                continue
            if tracker.is_temp_artifact(name):
                continue
              # Extract extension without leading dot
            if "." in name_lower:
                ext = name_lower.rsplit(".", 1)[-1]
                if ext in IMAGE_EXTS:
                    files.append(entry)
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
         + "      " + tags_line + "\n\n"
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

    tags_arr      = []
    ocr_arr       = []
    entities_arr = []
    caption_str   = ""
    quality_val   = 0
    emb_vec       = []
    parse_error   = None
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
# Legacy KB layer -- disabled. Keep for reference, but do not execute.
'''
# ============================================================================

def iso_to_epoch(iso_str):
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except Exception:
        return 0.0


def abs_key(rec):
    """Tracker key: absolute source path (filename fallback)."""
    src = rec.get("filepath") or rec.get("filename", "")
    return tracker.file_key(src)


def load_recs():
    """Parse _annotations.jsonl into {tracker_key: record}, dedup by key."""
    by_key = {}
    if not ANNOT_PATH.exists():
        eprint("ERROR: no _annotations.jsonl found. Run backend.py first.")
        sys.exit(1)
    for line in open(ANNOT_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        by_key[abs_key(rec)] = rec
    return by_key


def create_schema(conn):
    """Create all tables if absent (incremental -- no DROP of base tables)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS screenshots (
            id             INTEGER PRIMARY KEY,
            filename       TEXT NOT NULL,
            filepath       TEXT NOT NULL,
            mtime_iso      TEXT NOT NULL,
            mtime_epoch    REAL NOT NULL,
            caption        TEXT,
            quality_score  INTEGER CHECK(quality_score BETWEEN 0 AND 5)
           );
        CREATE TABLE IF NOT EXISTS tags (
            screenshot_id  INTEGER REFERENCES screenshots(id),
            tag            TEXT NOT NULL,
            UNIQUE(screenshot_id, tag)
           );
        CREATE TABLE IF NOT EXISTS ocr_lines (
            screenshot_id  INTEGER REFERENCES screenshots(id),
            line_text      TEXT NOT NULL,
            line_number    INTEGER
           );
        CREATE TABLE IF NOT EXISTS entities (
            screenshot_id  INTEGER REFERENCES screenshots(id),
            entity_name    TEXT NOT NULL
           );
        CREATE TABLE IF NOT EXISTS embeddings (
            screenshot_id  INTEGER UNIQUE REFERENCES screenshots(id),
            vector_blob    BLOB NOT NULL
           );
        CREATE VIRTUAL TABLE IF NOT EXISTS screenshots_fts
            USING fts5(caption, ocr, tag_list);
        CREATE TABLE IF NOT EXISTS tag_stats (
            tag            TEXT PRIMARY KEY,
            total_count    INTEGER NOT NULL
           );
        CREATE TABLE IF NOT EXISTS monthly_histogram (
            month            TEXT NOT NULL UNIQUE,
            screenshot_count INTEGER NOT NULL
           );
        CREATE INDEX IF NOT EXISTS idx_screenshots_mtime ON screenshots(mtime_epoch ASC);
        CREATE INDEX IF NOT EXISTS idx_tags_tag          ON tags(tag);
        CREATE INDEX IF NOT EXISTS idx_ocr_sid           ON ocr_lines(screenshot_id);
        CREATE INDEX IF NOT EXISTS idx_emb_sid           ON embeddings(screenshot_id);
    """)


def existing_screens(conn, force):
    """Return {filepath: (id, mtime_iso)} already ingested; empty when force."""
    if force:
        return {}
    out = {}
    for sid, fp, mtime_iso in conn.execute("SELECT id, filepath, mtime_iso FROM screenshots"):
        out[fp] = (sid, mtime_iso)
    return out


def delete_screenshot(conn, sid):
    """Remove a screenshot and all child rows (for a re-ingest)."""
    conn.execute("DELETE FROM tags       WHERE screenshot_id = ?", (sid,))
    conn.execute("DELETE FROM ocr_lines  WHERE screenshot_id = ?", (sid,))
    conn.execute("DELETE FROM entities   WHERE screenshot_id = ?", (sid,))
    conn.execute("DELETE FROM embeddings WHERE screenshot_id = ?", (sid,))
    if sid is not None:
        try:
            conn.execute("DELETE FROM screenshots_fts WHERE rowid = ?", (sid,))
        except sqlite3.OperationalError:
            pass
    conn.execute("DELETE FROM screenshots WHERE id = ?", (sid,))


def ingest_one(conn, rec, existing):
    """Insert/update one record. Returns (sid, changed:bool).

    A record is changed if its filepath is new, or its source mtime differs
    from the stored mtime_iso (an in-place edit). Unchanged records are a no-op.
    """
    fname         = rec.get("filename", "unknown")
    filepath      = rec.get("filepath", "") or ""
    raw           = rec.get("mtime_iso")
    mtime_iso     = raw if (isinstance(raw, str) and raw) else "1970-01-01T00:00:00+00:00"
    mtime_epoch = iso_to_epoch(mtime_iso)
    caption_str = (rec.get("caption") or "").strip()
    quality_val = int(rec.get("quality_score") or 0)

    prior = existing.get(filepath)
    if prior is not None and prior[1] == mtime_iso:
        return prior[0], False

    if prior is not None:
        delete_screenshot(conn, prior[0])

    cur = conn.cursor()
    cur.execute(
         "INSERT INTO screenshots "
         "(filename, filepath, mtime_iso, mtime_epoch, caption, quality_score) "
         "VALUES (?,?,?,?,?,?)",
         (fname, filepath, mtime_iso, mtime_epoch, caption_str, quality_val))
    sid = cur.lastrowid

    for tag in [str(t) for t in (rec.get("tags") or []) if str(t).strip()]:
        cur.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (sid, tag))
    for j, txt in enumerate(rec.get("OCR_text") or []):
        s = str(txt).strip()
        if s:
            cur.execute("INSERT OR IGNORE INTO ocr_lines VALUES (?,?,?)", (sid, s, j))
    for ent in [str(e).strip() for e in (rec.get("entities") or []) if str(e).strip()]:
        cur.execute("INSERT OR IGNORE INTO entities VALUES (?,?)", (sid, ent))

    emb_vec = rec.get("embedding_vector")
    if emb_vec and isinstance(emb_vec, list) and len(emb_vec):
        vec_bytes = struct.pack(f"{len(emb_vec)}f", *emb_vec)
        cur.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?)", (sid, vec_bytes))

    tags_set = sorted({str(t) for t in (rec.get("tags") or []) if str(t).strip()})
    ocr_str   = " ".join(str(t).strip() for t in (rec.get("OCR_text") or []))
    tags_str = ",".join(tags_set)
    if caption_str or ocr_str or tags_str:
        cur.execute(
             "INSERT INTO screenshots_fts(rowid, caption, ocr, tag_list) VALUES (?,?,?,?)",
             (sid, caption_str, ocr_str, tags_str))
    return sid, True


def rebuild_derived(conn):
    """Rebuild the cheap aggregate tables from the full DB (fast, O(n))."""
    tag_counter = {}
    conn.execute("DELETE FROM tag_stats")
    for tag, count in conn.execute("SELECT tag, COUNT(*) FROM tags GROUP BY tag"):
        conn.execute("INSERT INTO tag_stats VALUES (?,?)", (tag, count))
        tag_counter[tag] = count

    month_counts = {}
    for mtime_iso in conn.execute("SELECT mtime_iso FROM screenshots ORDER BY mtime_epoch ASC"):
        ym = str(mtime_iso)[:7]
        month_counts[ym] = month_counts.get(ym, 0) + 1
    conn.execute("DELETE FROM monthly_histogram")
    for month, count in sorted(month_counts.items()):
        conn.execute("INSERT INTO monthly_histogram VALUES (?,?)", (month, str(count)))
    return tag_counter


def write_exports(conn, tag_counter):
    """Write the flat NDJSON dump + tag co-occurrence index from the full DB."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    wiki_path = OUTPUT_DIR / "wiki.ndjson"
    wiki_tmp = OUTPUT_DIR / "wiki.ndjson.tmp"
    with open(wiki_tmp, "w", encoding="utf-8") as fh:
        for sid, fname, path, mtime_iso, mtime_epoch, caption, quality in conn.execute(
             "SELECT id, filename, filepath, mtime_iso, mtime_epoch, caption, "
             "quality_score FROM screenshots ORDER BY mtime_epoch ASC"):
            out = {
                 "sid": sid, "filename": fname, "filepath": path,
                 "mtime_iso": mtime_iso, "mtime_epoch": mtime_epoch,
                 "caption": caption, "quality": quality,
             }
            fh.write(json.dumps(out) + "\n")
    os.replace(wiki_tmp, wiki_path)

    sid_tags_by_id = {}
    for sid, tag in conn.execute("SELECT screenshot_id, tag FROM tags ORDER BY screenshot_id"):
        sid_tags_by_id.setdefault(sid, []).append(tag)

    tag_pairs = {}
    for tags_l in sid_tags_by_id.values():
        utags = sorted(set(tags_l))
        for i in range(len(utags)):
            for j in range(i + 1, len(utags)):
                p = (utags[i], utags[j])
                tag_pairs[p] = tag_pairs.get(p, 0) + 1

    edges = [{"source": t1, "target": t2, "weight": c}
             for (t1, t2), c in sorted(tag_pairs.items(), key=lambda x: -x[1])[:300]]
    top_tags_list = [{"tag": t, "count": c}
                     for t, c in sorted(tag_counter.items(), key=lambda x: -x[1])[:50]]

    tags_index_data = {
         "total_screenshots": "",   # filled below
         "unique_tags": len(tag_counter),
         "top_tags":      top_tags_list,
         "edges":         edges,
    }
    tags_index_data["total_screenshots"] = conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0]
    tags_path = OUTPUT_DIR / "tags_index.json"
    tags_tmp = OUTPUT_DIR / "tags_index.json.tmp"
    with open(tags_tmp, "w", encoding="utf-8") as fh:
        json.dump(tags_index_data, fh, indent=2)
    os.replace(tags_tmp, tags_path)
    return tags_index_data


def generate_thumbnails(recs_by_key, files, make_thumbs):
    """Generate 320px thumbs for any annotated file that lacks one.

    Tracked + incremental: an on-disk-but-unrecorded thumb is adopted (recorded
    in the tracker but not regenerated); a file already ok with its thumb on disk
    is a no-op; a new/missing thumb is generated via sips.
    """
    if not make_thumbs:
        eprint("Skipping thumbnails (--no-thumbs).")
        return 0
    thumb_dir = OUTPUT_DIR / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    def _dest_for(rec):
        stem = os.path.splitext(rec.get("filename", "x"))[0]
        return str(thumb_dir / (stem + ".jpg"))

    def _now():
        return datetime.now(tz=timezone.utc).isoformat()

    def _make_thumb(rec):
        key     = abs_key(rec)
        src     = rec.get("filepath", "")
        dest    = _dest_for(rec)
         # Register the entry so subsequent mark_* mutate the same record.
        files.setdefault(key, tracker.new_entry(rec.get("filename", key), rec.get("mtime_iso")))
         # Already recorded ok on disk -> no-op.
        if files[key].get("thumb_status") == "ok" and os.path.isfile(dest):
            return None
          # On disk but unrecorded -> adopt it (record ok, do not regenerate).
        if os.path.isfile(dest):
            tracker.mark_thumbnail(files, key, _now(), "ok")
            return "adopted"
        if not src or not os.path.isfile(src):
            tracker.mark_thumbnail(files, key, None, "fail")
            return None
        ext = os.path.splitext(rec.get("filename", ""))[1].lower()
        try:
            tmp = dest + ".part"
            cmd = ["/usr/bin/sips", "-Z", "320"]
            if ext == ".heic":
                cmd += ["-s", "format", "jpeg"]
            cmd += [src, "--out", tmp]
            subprocess.run(cmd, capture_output=True, timeout=30)
            if os.path.isfile(tmp):
                os.replace(tmp, dest)
                tracker.mark_thumbnail(files, key, _now(), "ok")
                return "generated"
            tracker.mark_thumbnail(files, key, None, "fail")
            return None
        except Exception:
            tracker.mark_thumbnail(files, key, None, "fail")
            return None

    generated = 0
    for rec in recs_by_key.values():
        r = _make_thumb(rec)
        if r in ("generated", "adopted"):
            generated += 1
    eprint(f"Thumbnails: {generated} generated/adopted, rest skipped.")
    return generated


# ============================================================================
# End of disabled legacy KB layer.
'''
# Per-environment override + pipeline driver
# ============================================================================

def configure_globals(config):
    """Apply one resolved environment to this module's globals."""
    global OLLAMA_BASE, VISION_MODEL, IMAGE_EXTS, MAX_DIM, TEMP_DIR
    global ANNOT2_PATH
    OLLAMA_BASE = config["ollama_base"]
    VISION_MODEL = config["vision_model"]
    IMAGE_EXTS = set(config["supported_images"])
    MAX_DIM = config["max_dim"]
    TEMP_DIR = config["temp_dir"]
    ANNOT2_PATH = config["env_dir"] / "annotation2.json"


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


def load_simple_annotations():
    """Load the path/filename/response records from annotation2.json."""
    if not ANNOT2_PATH.exists():
        return []
    try:
        with open(ANNOT2_PATH, encoding="utf-8") as annotations:
            records = json.load(annotations)
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    return records if isinstance(records, list) else []


def save_simple_annotations(records):
    """Atomically save the simple vision responses."""
    ANNOT2_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ANNOT2_PATH.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as annotations:
        json.dump(records, annotations, ensure_ascii=False, indent=2)
    os.replace(temp_path, ANNOT2_PATH)


def process_pending_generic(config, files, all_images, count_limit, deadline):
    """Describe each new image once and store the raw response."""
    records = load_simple_annotations()
    known_paths = {record.get("path") for record in records
                   if isinstance(record, dict)}
    pending = [path for path in all_images if os.path.abspath(path) not in known_paths]
    if count_limit is not None and count_limit > 0:
        pending = pending[:count_limit]

    processed = 0
    errors = 0
    started = time.monotonic()
    prompt = "what's on this picture"

    for image_path in pending:
        if deadline and datetime.now() >= deadline:
            break
        key = tracker.file_key(image_path)
        filename = os.path.basename(image_path)
        files.setdefault(key, tracker.new_entry(filename, None))
        tracker.mark_start(files, key)
        print("[%d/%d of %d] %s" %
              (processed + 1, len(pending), len(all_images), filename), flush=True)

        image_started = time.monotonic()
        response = ollama_vision(image_path, prompt, retries=1)
        elapsed = round(time.monotonic() - image_started, 3)
        error = None if response else "vision: no result"
        records.append({
            "path": os.path.abspath(image_path),
            "filename": filename,
            "response": response or "",
        })
        save_simple_annotations(records)
        tracker.mark_finish(
            files, key,
            vision_latency_s=elapsed,
            tags_count=None,
            embedding_dims=None,
            ok=bool(response),
            error=error,
            finished_at=datetime.now().astimezone().isoformat(),
        )
        save_progress(config, files, count_limit, len(all_images), 0,
                      processed + 1, int(bool(error)),
                      "completed" if not error else "error")
        processed += 1
        errors += int(bool(error))
        print("    %s (%.3fs)" % ("error" if error else "ok", elapsed), flush=True)

    status = "deadline-reached" if deadline and datetime.now() >= deadline else "completed"
    save_progress(config, files, count_limit, len(all_images), 0,
                  processed, errors, status)
    print("Done. %d image(s), %d error(s) in %.1fs." %
          (processed, errors, time.monotonic() - started), file=sys.stderr)


def process_pending(config, files, all_images, new_count, count_limit, deadline, no_thumbs, conn):
    pending = [
        path for path in all_images
        if files.get(tracker.file_key(path), {}).get("finished_at") is None
        and files.get(tracker.file_key(path), {}).get("processed_at") is None
     ]
    if count_limit is not None and count_limit > 0:
        pending = pending[:count_limit]

    existing = existing_screens(conn, False)
    prompt = prompt_text()
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
            record, ok, error = classify_one(image_path, prompt)
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

            sid, changed = ingest_one(conn, record, existing)
            if changed:
                existing[record.get("filepath", "")] = (sid, record.get("mtime_iso"))
            tracker.mark_ingested(files, key)
            if not no_thumbs:
                before = files[key].get("thumb_status")
                generate_thumbnails({key: record}, files, True)
                if before != "ok" and files[key].get("thumb_status") == "ok":
                    thumbnails += 1

            conn.commit()
            tag_counter = rebuild_derived(conn)
            write_exports(conn, tag_counter)
            conn.commit()
            processed += 1
            errors += int(bool(error))
            save_progress(config, files, count_limit, len(all_images), new_count,
                          processed, errors, "completed")
            print("    %s | tags=%d (%.3fs)" %
                   ("error" if error else ("ok" if ok else "fail"),
                    len(record["tags"]), elapsed), flush=True)

    status = "deadline-reached" if deadline and datetime.now() >= deadline else "completed"
    save_progress(config, files, count_limit, len(all_images), new_count,
                  processed, errors, status)
    print("Done. %d image(s), %d error(s), %d thumbnail(s) in %.1fs." %
           (processed, errors, thumbnails, time.monotonic() - started), file=sys.stderr)


def rebuild_kb(config, files, force, no_thumbs):
    records = load_recs()
    conn = sqlite3.connect(str(config["db_path"]))
    try:
        create_schema(conn)
        existing = existing_screens(conn, force)
        if force:
            for sid, _mtime in list(existing.values()):
                delete_screenshot(conn, sid)
            existing = {}
        for key, record in records.items():
            sid, changed = ingest_one(conn, record, existing)
            if changed:
                existing[record.get("filepath", "")] = (sid, record.get("mtime_iso"))
            files[key] = files.get(key) or tracker.new_entry(
                record.get("filename", key), record.get("mtime_iso"))
            tracker.mark_ingested(files, key)
        tag_counter = rebuild_derived(conn)
        write_exports(conn, tag_counter)
        conn.commit()
        if not no_thumbs:
            generate_thumbnails(records, files, True)
        save_progress(config, files, None, len(records), 0, 0, 0, "rebuild-complete")
    finally:
        conn.close()


def run(args):
    _env, config = config_loader.resolve_environment(args.env)
    configure_globals(config)
    lock = acquire_lock(config["env_dir"] / LOCK_NAME, args.wait)
    if lock is None:
        print("Another pipeline run is active for %s." % args.env, file=sys.stderr)
        return 0
    try:
        payload = tracker.load_registry(config["tracker_path"])
        files = payload["files"]
        source_dir = args.screenshot_dir or config["source_dir"]
        count_limit = config["processed_limit"] if args.count is None else args.count
        images = list_images(source_dir)
        new_count, _unprocessed = tracker.reconcile(
            source_dir, set(config["supported_images"]), files)
         # Persist the complete discovered list before starting processing.
        tracker.save_tracker(
            config["tracker_path"],
             {"files": files, "runs": tracker.build_summary(
                files, count_limit, len(images), new_this_run=new_count,
                status="reconciled")},
         )

        deadline = resolve_deadline(args.until)
        process_pending_generic(config, files, images, count_limit, deadline)
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
