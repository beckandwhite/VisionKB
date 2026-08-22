#!/usr/bin/env python3
"""
build_kb.py -- Ingest _annotations.jsonl into the SQLite knowledgebase.

This stage is INCREMENTAL: re-running re-does only new/changed work and
leaves everything else alone -- no DB drop, no thumbnail regeneration.

Creates / refreshes:
.workspace/<env>/wiki.db          SQLite FTS5 + embeddings (new/changed only)
    .workspace/<env>/wiki.ndjson    flat NDJSON dump of all annotations
    .workspace/<env>/tags_index.json tag frequency + co-occurrence graph
    .workspace/<env>/thumbnails/<stem>.jpg  320px thumbs (generated only if missing)

Progress is recorded into the shared tracker (_tracker.json): each ingested
file gets ingested_at; each thumb gets thumb_at / thumb_status, so the
tracker is the single backlog + log across the whole pipeline.

This module is used by pipeline.py and is not a standalone entry point.
"""

import json
import os
import sqlite3
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import tracker
import config_loader

IMG_EXTS      = {".png", ".jpg", ".jpeg", ".heic"}
SCRIPT_DIR = Path(__file__).resolve().parent
_, _CONFIG = config_loader.resolve_environment()
DB_PATH = _CONFIG["db_path"]
ANNOT_PATH = _CONFIG["annotations_path"]
TRACKER_PATH = _CONFIG["tracker_path"]
OUTPUT_DIR = _CONFIG["exports_dir"]


def eprint(*args):
    print(*args, flush=True, file=sys.stderr)


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
        eprint("ERROR: no _annotations.jsonl found. Run pipeline.py first.")
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
    fname        = rec.get("filename", "unknown")
    filepath     = rec.get("filepath", "") or ""
    raw          = rec.get("mtime_iso")
    mtime_iso    = raw if (isinstance(raw, str) and raw) else "1970-01-01T00:00:00+00:00"
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
    ocr_str  = " ".join(str(t).strip() for t in (rec.get("OCR_text") or []))
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
        "total_screenshots": "",  # filled below
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
        key    = abs_key(rec)
        src    = rec.get("filepath", "")
        dest   = _dest_for(rec)
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
    with ThreadPoolExecutor(max_workers=8) as pool:
        for f in as_completed([pool.submit(_make_thumb, rec) for rec in recs_by_key.values()]):
            r = f.result()
            if r in ("generated", "adopted"):
                generated += 1
    eprint(f"Thumbnails: {generated} generated/adopted, rest skipped.")
    return generated


def build(force=False, env="DEV"):
    """Incremental ingest of _annotations.jsonl into the SQLite KB + exports."""
    global DB_PATH, ANNOT_PATH, TRACKER_PATH, OUTPUT_DIR
    _, config = config_loader.resolve_environment(env)
    DB_PATH = config["db_path"]
    ANNOT_PATH = config["annotations_path"]
    TRACKER_PATH = config["tracker_path"]
    OUTPUT_DIR = config["exports_dir"]
    eprint(f"Loading annotations from {ANNOT_PATH}...")
    recs_by_key = load_recs()
    eprint(f"       {len(recs_by_key)} unique records loaded.")

    payload = tracker.load_registry(TRACKER_PATH)
    files = payload["files"]

    eprint("Opening database...")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    create_schema(conn)

    existing = existing_screens(conn, force)
    if force:
        eprint("--force: clearing existing screenshots for a full rebuild.")
        for sid, _ in list(existing.values()):
            delete_screenshot(conn, sid)
        existing = {}

    eprint(f"Ingesting {len(recs_by_key)} screenshot(s) (incremental)...")
    ingested_count = 0
    for key, rec in recs_by_key.items():
        _sid, changed = ingest_one(conn, rec, existing)
        if changed:
            ingested_count += 1
        marker = files.get(key) or tracker.new_entry(
            rec.get("filename", key), rec.get("mtime_iso"))
        files[key] = marker
        tracker.mark_ingested(files, key)
    conn.commit()
    eprint(f"       {ingested_count} new/changed, {len(recs_by_key) - ingested_count} unchanged.")

    if ingested_count or force:
        eprint("Rebuilding derived tables + exports...")
        tag_counter = rebuild_derived(conn)
        write_exports(conn, tag_counter)
        conn.commit()
    else:
        eprint("Nothing changed -- skipping derived rebuild.")
        tag_counter = {}

    generated = generate_thumbnails(
        recs_by_key, files, make_thumbs="--no-thumbs" not in sys.argv)

    runs = tracker.build_summary(
        files, 0, len(recs_by_key), new_this_run=0, processed_this_run=len(recs_by_key),
        errors_this_run=0, status="build-complete")
    runs["last_build_at"]       = datetime.now(tz=timezone.utc).isoformat()
    runs["ingested_this_run"]   = ingested_count
    runs["thumbnails_this_run"] = generated
    tracker.save_tracker(TRACKER_PATH, {"files": files, "runs": runs})

    print("\n=== Build Complete (incremental) ===")
    print(f"   Database:       {os.path.getsize(DB_PATH) / 1024:.1f} KB")
    print(f"   Ingested now:   {ingested_count} new/changed of {len(recs_by_key)}")
    print(f"   New thumbs:     {generated}")
    if tag_counter:
        top_tag = max(tag_counter, key=tag_counter.get)
        print(f"   Top tag:        {top_tag} ({tag_counter.get(top_tag, 0)})")
    elif ingested_count == 0 and generated == 0:
        print("   Already up to date -- nothing to do.")


if __name__ == "__main__":
    env = "DEV"
    if "-env" in sys.argv:
        env_index = sys.argv.index("-env")
        if env_index + 1 >= len(sys.argv):
            raise SystemExit("-env requires an environment name")
        env = sys.argv[env_index + 1]
    build(force="--force" in sys.argv, env=env)
