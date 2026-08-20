#!/usr/bin/env python3
"""
build_kb.py -- Ingest _annotations.jsonl into SQLite knowledgebase DB.

Creates:
  exports/wiki.ndjson            -- Flattened NDJSON dump of all annotations
  exports/tags_index.json        -- Tag frequency + co-occurrence graph adjacency list

Usage:
    python3 kb/build_kb.py               Build everything from _annotations.jsonl
    python3 kb/build_kb.py --no-thumbs   Skip thumbnail generation for speed
"""

import argparse
import json
import os
import sqlite3
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

IMG_EXTS = {".png", ".jpg", ".jpeg", ".heic"}
SCRIPT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = SCRIPT_DIR / "kb" / "data" / "wiki.db"
ANNOT_PATH = SCRIPT_DIR / "_annotations.jsonl"
OUTPUT_DIR = SCRIPT_DIR / "exports"


def eprint(*args):
    print(*args, flush=True, file=sys.stderr)


def iso_to_epoch(iso_str: str) -> float:
    try:
        return datetime.fromisoformat(iso_str).timestamp()
    except Exception:
        return 0.0


def build():
    """Main entry point: ingest _annotations.jsonl into SQLite + exports."""
    eprint(f"Loading annotations from {ANNOT_PATH}...")

    if not ANNOT_PATH.exists():
        eprint("ERROR: no _annotations.jsonl found. Run classify_images.py first.")
        sys.exit(1)

    recs = []
    for line in open(ANNOT_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    eprint(f"   {len(recs)} records loaded.")

    eprint("Creating database...")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        os.remove(DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    conn.executescript("""
        CREATE TABLE screenshots (
            id              INTEGER PRIMARY KEY,
            filename        TEXT NOT NULL,
            filepath        TEXT NOT NULL,
            mtime_iso       TEXT NOT NULL,
            mtime_epoch     REAL NOT NULL,
            caption         TEXT,
            quality_score   INTEGER CHECK(quality_score BETWEEN 0 AND 5)
         );

        CREATE TABLE tags (
            screenshot_id   INTEGER REFERENCES screenshots(id),
            tag             TEXT NOT NULL,
            UNIQUE(screenshot_id, tag)
         );

        CREATE TABLE ocr_lines (
            screenshot_id   INTEGER REFERENCES screenshots(id),
            line_text       TEXT NOT NULL,
            line_number     INTEGER
         );

        CREATE TABLE entities (
            screenshot_id   INTEGER REFERENCES screenshots(id),
            entity_name     TEXT NOT NULL
         );

        CREATE TABLE embeddings (
            screenshot_id   INTEGER UNIQUE REFERENCES screenshots(id),
            vector_blob     BLOB NOT NULL
         );

        DROP TABLE IF EXISTS screenshots_fts;
        CREATE VIRTUAL TABLE screenshots_fts USING fts5(
            caption, ocr, tag_list
          );

        DROP TABLE IF EXISTS tag_stats;
        CREATE TABLE tag_stats (
            tag             TEXT PRIMARY KEY,
            total_count     INTEGER NOT NULL
         );

        DROP TABLE IF EXISTS monthly_histogram;
        CREATE TABLE monthly_histogram (
            month           TEXT NOT NULL UNIQUE,
            screenshot_count INTEGER NOT NULL
         );

        CREATE INDEX idx_screenshots_mtime ON screenshots(mtime_epoch ASC);
        CREATE INDEX idx_tags_tag ON tags(tag);
        CREATE INDEX idx_ocr_sid ON ocr_lines(screenshot_id);
        CREATE INDEX idx_emb_sid ON embeddings(screenshot_id);
    """)

    eprint(f"Importing {len(recs)} screenshots...")

    s_rows       = []   # All screenshot entries: (id, filename, filepath, mtime_iso, mtime_epoch, caption, quality)

    sid_tags     = {}
    sid_ocr      = {}
    sid_ent      = {}

    for i, rec in enumerate(recs):
        fname = rec.get("filename", f"unknown-{i}")
        filepath = rec.get("filepath", "") or ""
        
        mtime_iso_raw = rec.get("mtime_iso")
        if isinstance(mtime_iso_raw, str) and len(mtime_iso_raw) > 0:
            mtime_iso = mtime_iso_raw
        else:
            mtime_iso = "1970-01-01T00:00:00+00:00"

        mtime_epoch = iso_to_epoch(mtime_iso)
        caption_str = (rec.get("caption") or "").strip()
        quality_val = int(rec.get("quality_score") or 0)

        s_rows.append((i, fname, filepath, mtime_iso, mtime_epoch, caption_str, quality_val))
        sid_tags[i]       = [str(t) for t in (rec.get("tags") or []) if str(t).strip()]
        sid_ocr[i]        = [(str(t.strip()), j) for j, t in enumerate(rec.get("OCR_text") or []) if str(t).strip()]
        sid_ent[i]        = [str(e).strip() for e in (rec.get("entities") or []) if str(e).strip()]

    cur.executemany(
        "INSERT INTO screenshots VALUES (?,?,?,?,?,?,?)",
        s_rows
    )

    for sid, tags_list in sid_tags.items():
        for tag in tags_list:
            cur.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (sid, tag))

    for sid, ocr_list in sid_ocr.items():
        for text, lineno in ocr_list:
            cur.execute("INSERT OR IGNORE INTO ocr_lines VALUES (?,?,?)", (sid, text, lineno))

    for sid, ent_list in sid_ent.items():
        for entity in ent_list:
            cur.execute("INSERT OR IGNORE INTO entities VALUES (?,?)", (sid, entity))

    conn.commit()

    eprint("Processing embeddings...")
    emb_rows = []
    for i, rec in enumerate(recs):
        emb_vec = rec.get("embedding_vector")
        if emb_vec and isinstance(emb_vec, list) and len(emb_vec):
            vec_bytes = struct.pack(f"{len(emb_vec)}f", *emb_vec)
            emb_rows.append((i, vec_bytes))
    cur.executemany("INSERT INTO embeddings VALUES (?,?)", emb_rows)
    conn.commit()

    eprint("Building FTS5 index...")
    fts_rows = []
    tag_counter = {}
    for sid in range(len(s_rows)):
        rec = recs[sid]
        cap = (rec.get("caption") or "").strip()
        tags_list = set(sid_tags.get(sid, []))
        tags_str = ",".join(sorted(tags_list))
        ocr_str = " ".join(o[0] for o in sid_ocr.get(sid, []))
        if not (cap or ocr_str or tags_str):
            continue
        fts_rows.append((sid, cap, ocr_str, tags_str))
        for t in tags_list:
            tag_counter[t] = tag_counter.get(t, 0) + 1

    cur.execute("DELETE FROM screenshots_fts")
    cur.executemany(
        "INSERT INTO screenshots_fts(rowid, caption, ocr, tag_list) VALUES (?,?,?,?)",
        fts_rows,
    )
    conn.commit()

    eprint("Tag stats...")

     # Tag stats
    conn.execute("DELETE FROM tag_stats")
    for tag, count in tag_counter.items():
        conn.execute(
             "INSERT INTO tag_stats VALUES (?,?)", 
              (tag, count))
    conn.commit()

      # Monthly histogram
    month_counts = {}
    for row in s_rows:
        mtime_iso = row[3]
        try:
            ym = str(mtime_iso)[:7]
            month_counts[ym] = month_counts.get(ym, 0) + 1
        except (IndexError, TypeError):
            continue
    eprint(f"Monthly histogram: {len(month_counts)} months")
    conn.execute("DELETE FROM monthly_histogram")
    for month, count in sorted(month_counts.items()):
        conn.execute(
             "INSERT INTO monthly_histogram VALUES (?,?)", 
              (month, str(count)))
    conn.commit()

      # Wiki ndjson export
    eprint(f"Building wiki.ndjson ({len(recs)} entries)...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / "wiki.ndjson", "w") as fh:
        for row in s_rows:
            sid = row[0]
            mtime_iso = row[3]
            mtime_epoch = row[4]
            caption = row[5]
            quality = row[6]
            rec = recs[sid]
            fname = rec.get("filename", f"unknown-{sid}")
            path   = rec.get("filepath", "") or ""
            out = json.dumps({
                  "sid": sid,
                  "filename": fname,
                  "filepath": path,
                  "mtime_iso": mtime_iso,
                  "mtime_epoch": mtime_epoch,
                  "caption": caption,
                  "quality": quality,
              })
            fh.write(out + "\n")

     # Tag co-occurrence edges export
    eprint(f"Building tags_index.json...")
    
    tag_pairs = {}
    for sid in range(len(s_rows)):
        utags = sorted(set(sid_tags.get(sid, [])))
        for i in range(len(utags)):
            for j in range(i + 1, len(utags)):
                p = (utags[i], utags[j])
                tag_pairs[p] = tag_pairs.get(p, 0) + 1

    edges = [{"source": t1, "target": t2, "weight": c}
             for (t1, t2), c in sorted(tag_pairs.items(), key=lambda x: -x[1])[:300]]

    top_tags_list = [{"tag": t, "count": c} 
                     for t, c in sorted(tag_counter.items(), key=lambda x: -x[1])[:50]]

    tags_index_data = {
        "total_screenshots": len(s_rows),
        "unique_tags":       len(tag_pairs),
        "top_tags":         top_tags_list,
        "edges":            edges,
    }

    with open(OUTPUT_DIR / "tags_index.json", "w") as fh:
        json.dump(tags_index_data, fh, indent=2)

      # Thumbnail generation (if not skipping)
    thumb_dir = OUTPUT_DIR / "thumbnails"
    if len(recs) > 0 and "--no-thumbs" not in sys.argv:
        eprint("Generating thumbnails...")
        thumb_dir.mkdir(parents=True, exist_ok=True)

        def _make_thumb(rec):
            src = rec.get("filepath", "")
            if not src or not os.path.isfile(src):
                return None
            ext = os.path.splitext(rec.get("filename", ""))[1].lower()
            dest = str(thumb_dir / (os.path.splitext(rec.get("filename", "x"))[0] + ".jpg"))
            if os.path.isfile(dest):
                return dest
            try:
                cmd = ["/usr/bin/sips", "-Z", "320"]
                if ext == ".heic":
                    cmd += ["-s", "format", "jpeg"]
                cmd += [src, "--out", dest]
                subprocess.run(cmd, capture_output=True, timeout=30)
                return dest if os.path.isfile(dest) else None
            except Exception:
                return None

        made = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            for f in as_completed([pool.submit(_make_thumb, recs[i])
                                   for i in range(len(recs))]):
                if f.result() is not None:
                    made += 1
        eprint(f"Generated {made} thumbnails.")
    else:
        eprint("Skipping thumbnails (--no-thumbs or empty).")

    print("\n=== Build Complete ===")
    top_tag = max(tag_counter, key=tag_counter.get) if tag_counter else ""
    print(f"   Database:        {os.path.getsize(DB_PATH) / 1024:.1f} KB")
    print(f"   Records:         {len(s_rows)} annotations ingested")
    print(f"   Unique tags:     {len(tag_counter)}")
    print(f"   Edges:           {len(edges)} tag co-occurrence pairs")
    if top_tag:
        print(f"   Top tag:         {top_tag} ({tag_counter.get(top_tag, 0)})")


if __name__ == "__main__":
    build()
