#!/usr/bin/env python3
"""
Screenshot classifier using Ollama vision model + embeddings.

Outputs:
     _annotations.jsonl    (one JSON line per image; appended, never rewritten)
     _tracker.json         (per-file registry + run summary, the progress ledger;
                             saved atomically every SAVE_EVERY files)
     telemetry.log         (append-only performance metrics)

The tracker is a self-maintaining registry, not a bare index. Each run:
  1. reconciles the source folder into the tracker (filename + mtime per file,
     keyed by absolute path), appending any files that are new since the last run;
  2. marks every already-processed file with a processed_at timestamp (progress);
  3. classifies the next `--count` UNPROCESSED files (newest mtime first).
Files already annotated in _annotations.jsonl are auto-marked processed on first
reconcile so they are not reclassified.

Usage:
    python3 classify_images.py --count 50                        # classify next 50 unprocessed
    python3 classify_images.py --screenshot-dir '/path/folder'   # scan a custom folder
    python3 classify_images.py                                   # all remaining, default iCloud folder
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


# ============================================================================
# Constants
# ============================================================================

OLLAMA_BASE = "http://127.0.0.1:11434"
VISION_MODEL = "muse-glimmer:30b-mlx"
EMBED_MODEL  = "nomic-embed-text:latest"
IMAGE_EXTS   = {"png", "jpg", "jpeg", "heic"}

SAVE_EVERY   = 25           # save checkpoint every N processed files
MAX_DIM      = 2560          # resize images longer than this for OOM safety


# ============================================================================
# Scene tag taxonomy (injected into the vision prompt)
# ============================================================================

SCENE_TAGS_TEXT = r"""art-gallery museum-exhibit art-studio painting-in-progress sculpture-display
business-meeting conference-call webinar boardroom pitch-presentation
breakroom-kitchen pantry fridge-magnetotes microwave-use counter-setup
charity-event fundraiser donation-drive volunteer-work community-outreach
classroom lecture-hall whiteboard-session student-desk exam-room
coding-dev debugging ide-screen git-repo terminal-shell api-development
commuting-transit subway-rider bus-window train-station airport-terminal
conference-booth expo-show trade-show speaker-stage attendee-networking
cooking-chef food-prep recipe-following kitchen-counter plating-presentation
coworking-space open-office hot-desk remote-setup shared-desk
customer-support help-desk ticket-dashboard chat-interface phone-support
dashboard-analytics kpi-metrics chart-graph report-card performance-monitor
data-analysis spreadsheet csv-viewer sql-query data-visualization
dining-restaurant cafe-cafe bar-pub food-court fine-dining
emergency-room clinic-waiting hospital-corridor medical-equipment triage-desk
exercise-gym yoga-studio running-trail weight-lifting cycling-path
factory-manufacturing assembly-line quality-inspection warehouse-logistics
family-gathering holiday-celebration birthday-party reunion-event family-photo
farm-agriculture field-harvest barn-farm livestock-pen garden-plot
fitness-tracking smartwatch-screen activity-app health-dashboard workout-plan
game-playing board-game video-game card-game puzzle-solver
game-streaming live-stream-setup game-overlay chat-panel twitch-interface
gift-wrapping party-supplies invitation-card favor-bag ceremony-decor
graphic-design photoshop-editor illustrator-artwork brand-guidelines
grocery-shopping aisle-select produce-section checkout-counter cart-loading
gymnastics-sports track-field swimming-pool court-tennis gym-floor
haircut-styling salon-chair mirror-reflection product-application
home-office desk-setup monitor-array bookshelf plant-decor
hospitality-hotel front-desk lobby-lounge room-service pool-area
hunting-outdoors wildlife-camera hunter-stand fishing-dock bird-watching
incident-response war-room status-wall pager-alert post-mortem-meeting
internet-browsing search-engine news-site social-media-feed forum-thread
jewelry-shopping jewelry-display ring-selection watch-collection gemstone-loupe
kitchen-cooking stove-top oven-use sink-washing fridge-organizing
laboratory-science microscope-view lab-bench test-tube-rack experiment-notebook
laundry-room washing-machine dryer-loading ironing-board folding-table
learning-mobile online-course tutorial-video quiz-screen reading-app
living-room-lounge couch-seating tv-screen coffee-table fireplace
logistics-warehouse forklift-operation pallet-stack shipping-label inventory-count
makeup-beauty mirror-application cosmetics-display skincare-routine brush-use
meeting-room conference-table video-call-screen whiteboard-notes projector-slide
music-concert live-band dj-booth speaker-stack crowd-photo
museum-exhibit gallery-walk art-piece-closeup exhibit-curator-info artifact-display
news-press press-briefing red-carpet interview-room photographer-shooting
notes-study highlighter-notes flashcard-fan textbook-open index-card-stack
office-work cubicle-desk monitor-multi-monitor filing-cabinet printer-use
ordering-online e-commerce-cart checkout-page product-listing wishlist-page
outdoor-adventure hiking-trail campsite-tent kayak-lake rock-climbing-wall
parks-recreation playground-slide picnic-table fountain-spray bench-reading
parenting-family baby-monitor toy-sort stroller-walk diaper-change
personal-finance budget-sheet expense-track portfolio-view tax-document
pet-care dog-park vet-clinic pet-food-bag grooming-saloon
photo-editing camera-raw lightroom-panel cropping-tool color-grading
poetry-literary notebook-writing published-poem open-mic-stage book-page-closeup
product-design wireframe-sketch mockup-review user-journey-map design-system-doc
product-launch demo-stage investor-present press-release-handout early-access
project-management kanban-board sprint-timeline jira-dashboard gantt-chart
public-speaking podium-mic audience-seating projector-screen teleprompter-read
quality-assurance test-case-list bug-report regression-suite uat-session
radio-broadcast mic-boom-shots console-panel studio-glass live-indicator
reading-leisure novel-handheld kindle-reader library-shelf magazine-open
research-research literature-review citation-manager hypothesis-note data-collection
retail-store shelf-display checkout-lane fitting-room display-window
restaurant-kitchen chef-line expo-pass dining-floor bar-area
safety-training ppe-gear safety-sign first-aid-kit drill-exercise
salon-spa treatment-table massage-chair sauna-room product-shelf
sales-pipeline crm-dashboard deal-funnel forecast-chart client-list
scenic-nature mountain-view ocean-waves forest-path sunset-sky
security-watch cctv-monitor access-control-panel badge-reader incident-log
server-room rack-server cable-management climate-control ups-system
shopping-mall storefront-window mall-atrium food-hall-table escalator-descent
skill-building workshop-table certification-badge online-module practice-exercise
soccer-sports goal-post sideline-bench scoreboard field-markings
sound-studio mixing-console mic-cable vocal-booth audio-waveform
sports-bar bar-counter-view tv-multi-screen team-merch-display game-day-board
stadium-event crowd-cheering field-level-view score-tower locker-room
startup-office nap-zone ping-pong-table standup-area demo-fridge
street-market vendor-booth street-food-cart artisan-stall bazaar-crowd
study-group study-desk group-discussion peer-review library-carrel
supermarket-store grocery-aisle deli-counter bakery-shelf checkout-queue
swimming-pool pool-edge-view lifeguard-chair diving-board lap-lane
tabletop-game board-piece-closeup card-hand-fan dice-roll-action miniatures-table
talent-hiring interview-room resume-reader coding-exam candidate-presentation
tax-prep w2-form deduction-checklist irs-guide calculator-pad
teaching-education chalkboard-lesson student-paper-review lecture-slide lab-demo
telehealth-video doctor-screen-view symptom-checklist prescription-print
tent-camping campfire-setup tent-interior hammock-rest trail-map-read
testing-lab spectrometer-view gel-electrophoresis hoods-fume microtome-cut
tracking-logging activity-feed timestamp-entry log-panel audit-trail
transport-vehicle cockpit-dashboard dashcam-view steering-wheel-closeup gear-shift
travel-airport baggage-claim boarding-gate security-line terminal-window
university-campus quad-pave-bench lecture-hall-seating campus-map-kiosk library-steps
vacation-beach shoreline-walk cabana-lounge surfboard-rest sun-shade-tent
vegan-organic food-display-counter farmers-market-stall produce-rack-planter
virtual-reality vr-headset-view controller-grip immersive-scene-shot lab-boundary-marker
webinar-training presentation-slide-share participant-grid chat-panel-live poll-question-box
workshop-maker soldering-iron-view 3d-printer-bed lathe-wheel-spin wood-bench-saw"""

TAG_LIST = sorted(t.strip() for t in SCENE_TAGS_TEXT.split() if t.strip())


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
    """Run /api/generate with image. Returns text or None on failure."""
    path = image_path

    # HEIC -> JPEG via sips
    if image_path.lower().endswith(".heic"):
        tmp = image_path + ".tmp.jpeg"
        try:
            ret = subprocess.run(
                ["/usr/bin/sips", "-s", "format", "jpeg", "-o", tmp, path],
                capture_output=True, timeout=30,
            )
            if ret.returncode == 0 and os.path.isfile(tmp):
                path = tmp
        except Exception:
            return None

    # Resize oversized images to avoid OOM
    try:
        w_out = subprocess.check_output(
            ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", path]
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
            resized = path + ".tmpresize.jpg"
            subprocess.check_call(
                ["/usr/bin/sips", "-Z", str(nw), str(nh), path, "--out", resized],
                timeout=30,
            )
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
            resp_data = ollama_post_json("/api/generate", payload, timeout_val=180)
            result = resp_data.get("response", "")
            if result:
                return result.strip()
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))

    # Clean up temp files
    try:
        for suff in [".tmp.jpeg", ".tmpresize.jpg"]:
            if image_path + suff == path or path.endswith(suff):
                os.unlink(path)
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
    """Scan directory for image files (no dots), return sorted by mtime desc."""
    files = []
    for entry in os.scandir(directory):
        if not entry.is_file():
            continue
        name_lower = entry.name.lower()
        if name_lower.startswith("."):
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
# Tracker registry (self-maintaining file list + progress ledger)
# ============================================================================

def file_key(path):
    """Stable identity for a source file: its absolute path."""
    return os.path.abspath(path)


def load_tracker(tracker_path):
    """Load the files map from _tracker.json. Returns {} on missing/old schema.

    Old flat index checkpoints (last_processed_index / index) are ignored; the
    registry is rebuilt from the folder + existing annotations on first run.
    """
    if not os.path.exists(tracker_path):
        return {}
    try:
        with open(tracker_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("files"), dict):
        return data["files"]
    return {}


def reconcile(directory, files):
    """Upsert every image in `directory` into the `files` map.

    Appends new files (status pending, processed_at null); refreshes mtime_iso on
    already-known files. Returns (new_count, unprocessed_count).
    """
    new_count = 0
    for path in list_images(directory):
        key = file_key(path)
        rec = files.get(key)
        if rec is None:
            mt = os.path.getmtime(path)
            files[key] = {
                "filename":      os.path.basename(path),
                "mtime_iso":      datetime.fromtimestamp(mt, tz=timezone.utc).isoformat(),
                "processed_at":   None,
                "quality_score":  None,
                "status":         "pending",
            }
            new_count += 1
        else:
            rec["mtime_iso"] = datetime.fromtimestamp(
                os.path.getmtime(path), tz=timezone.utc).isoformat()
    unprocessed = sum(1 for r in files.values() if r.get("processed_at") is None)
    return new_count, unprocessed


def seed_from_annotations(annot_path, files):
    """Mark files already present in _annotations.jsonl as processed.

    Idempotent: only fills processed_at for entries still pending. Returns the
    number of files newly seeded (already-annotated ⇒ skip reclassification).
    """
    if not os.path.exists(annot_path):
        return 0
    now = datetime.now(tz=timezone.utc).isoformat()
    seeded = 0
    with open(annot_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = file_key(rec.get("filepath", rec.get("filename", "")))
            entry = files.get(key)
            if entry is None or entry.get("processed_at") is not None:
                continue
            entry["processed_at"]    = now
            entry["quality_score"]   = rec.get("quality_score")
            entry["status"]          = "backfilled"
            seeded += 1
    return seeded


def mark_processed(files, key, quality_val, ok):
    """Stamp a file as just-processed in the registry."""
    entry = files.setdefault(key, {})
    entry["processed_at"]  = datetime.now(tz=timezone.utc).isoformat()
    entry["quality_score"] = quality_val
    entry["status"]        = "ok" if ok else "fail"


def save_tracker(tracker_path, files, runs_summary):
    """Atomically write the registry + run summary (write .tmp, then os.replace)."""
    payload = {"files": files, "runs": runs_summary}
    tmp = tracker_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, tracker_path)


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
        + "   " + tags_line + "\n\n"
        "- OCR_text: literal text visible in the image as an array of strings.\n"
        "- entities: notable named entities visible on screen as an array of strings.\n"
        "- caption: a one-sentence plain-English description of the screenshot.\n"
        "- quality_score: integer 1-5 (1=blurry/unusable, 5=crisp/readable).\n"
        "Return ONLY the JSON object with no markdown fences or extra text."
    )


# ============================================================================
# Build and process
# ============================================================================
def main(count_limit, screenshot_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tracker_path = os.path.join(script_dir, "_tracker.json")
    annot_path    = os.path.join(script_dir, "_annotations.jsonl")
    log_path      = os.path.join(script_dir, "telemetry.log")

    # Load the existing registry (empty if absent / old flat-index schema).
    files = load_tracker(tracker_path)
    migrated = len(files) == 0 and os.path.exists(tracker_path)

    # Refresh the folder list into the registry: append files new since the
    # last run and refresh mtime on known files. This is the "keep the list
    # current" step; progress is tracked via each file's processed_at.
    screenshot_dir = os.path.expanduser(screenshot_dir)
    all_images = list_images(screenshot_dir)
    total_images = len(all_images)
    new_count, unprocessed = reconcile(screenshot_dir, files)

    # Auto-mark already-annotated files as processed so they are not reclassified.
    seeded = seed_from_annotations(annot_path, files)
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
    # mtime-descending from list_images(); filtering preserves that order.
    pending = [p for p in all_images
               if files.get(file_key(p), {}).get("processed_at") is None]
    if count_limit > 0:
        batch = pending[:count_limit]
    else:
        batch = list(pending)

    # Write the refreshed registry even when there is nothing to do, so the
    # file list stays current across runs.
    def run_summary(processed_count, error_count, secs, status):
        unproc = sum(1 for r in files.values() if r.get("processed_at") is None)
        return {
            "last_run_at":        datetime.now(tz=timezone.utc).isoformat(),
            "last_count_param":   count_limit,
            "total_files":        total_images,
            "processed":          len(files) - unproc,
            "unprocessed":        unproc,
            "new_this_run":       new_count,
            "processed_this_run": processed_count,
            "errors_this_run":    error_count,
            "status":             status,
        }

    if not batch:
        print("Nothing to do: all files already processed.", file=sys.stderr)
        save_tracker(tracker_path, files,
                     run_summary(0, 0, 0.0, "nothing-to-process"))
        return

    prompt_str = prompt_text()
    processed_count = 0
    error_count = 0
    global_t0 = time.monotonic()

    with open(annot_path, "a", encoding="utf-8") as ann_fh:
        for i, img_path in enumerate(batch):
            t0 = time.monotonic()
            key    = file_key(img_path)
            bname = os.path.basename(img_path)
            mtime_iso = datetime.fromtimestamp(
                os.path.getmtime(img_path), tz=timezone.utc
            ).isoformat()
            # Make sure the scanned path is in the registry even if reconcile
            # skipped it for some reason.
            files.setdefault(key, {
                "filename":      bname,
                "mtime_iso":     mtime_iso,
                "processed_at":  None,
                "quality_score": None,
                "status":        "pending",
            })

            print(f"[{i + 1}/{len(batch)}] {bname}", flush=True)

            # Vision classification
            vis_text = ollama_vision(img_path, prompt_str)

            tags_arr       = []
            ocr_arr        = []
            entities_arr   = []
            caption_str    = ""
            quality_val    = 0

            if vis_text:
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
                    caption_str    = str(parsed.get("caption", ""))
                    quality_val    = int(parsed.get("quality_score", 0))
                except (json.JSONDecodeError, ValueError):
                    error_count += 1
                    print(f"   parse-error", flush=True)

            # Embedding: always call the model
            embed_source = caption_str or "\n".join(tags_arr) or bname
            emb_vec = ollama_embed(embed_source)

            entry = {
                "filename":       bname,
                "filepath":       os.path.abspath(img_path),
                "mtime_iso":      mtime_iso,
                "tags":           tags_arr,
                "OCR_text":       ocr_arr,
                "entities":       entities_arr,
                "caption":        caption_str,
                "quality_score":  quality_val,
                "embedding_vector": emb_vec,
            }

            ann_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            ann_fh.flush()
            processed_count += 1

            elapsed = round(time.monotonic() - t0, 3)
            ok = bool(tags_arr)
            status = "ok" if ok else "fail"
            print(f"    {status} | tags={len(tags_arr)} ocr={len(ocr_arr)} "
                  f"emb={len(emb_vec)}", flush=True)

            # Telemetry per-file log
            ts_now = datetime.now(tz=timezone.utc).isoformat()
            telem_line = json.dumps({
                "timestamp":      ts_now,
                "filename":       bname,
                "vision_latency_s": elapsed,
                "tags_count":     len(tags_arr),
                "embedding_dims": len(emb_vec),
                "status":         status,
            }) + "\n"
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(telem_line)

            # Mark processed + checkpoint (resumable on crash / interrupt).
            # processed_at is written the moment a file is done, so an interrupt
            # leaves a clean, accurate registry.
            mark_processed(files, key, quality_val, ok)
            total_done = i + 1
            if total_done % SAVE_EVERY == 0 or i == len(batch) - 1:
                save_tracker(tracker_path, files,
                             run_summary(processed_count, error_count,
                                         round(time.monotonic() - global_t0, 3),
                                         "completed"))
                # Brief pause after checkpoint so Ollama can cool down
                if i < len(batch) - 1:
                    time.sleep(0.3)

    overall_s = round(time.monotonic() - global_t0, 3)
    print(f"\nDone. {processed_count}/{len(batch)} in {overall_s}s.",
          file=sys.stderr)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=0,
                    help="Limit to N images (for testing)")
    p.add_argument("--screenshot-dir",
                    default=os.path.expanduser(
                        "~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/"),
                    help="Folder to scan for screenshots "
                         "(default: iCloud Screenshots)")
    args = p.parse_args()
    main(args.count, args.screenshot_dir)
