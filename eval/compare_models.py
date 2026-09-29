"""Vision-model eval harness orchestrator (issue #32).

For each (image x work x model) cell this:

1. runs the work's prompt (imported from ``work1``/``work2``/``work3``) through
   the candidate vision model via ``work_common.vision_request``, recording the
   output, wall-clock latency and any error -- resiliently, so one failing cell
   never aborts the run;
2. has an LLM-as-judge (see ``judges.py``) score the DERIVED TEXT ONLY -- never
   the image -- against ``eval/rubrics/work{1,2,3}.md``.

It then aggregates per-model mean scores per work, pairwise deltas, latency
percentiles and an overall ranking, and writes ``results.jsonl`` (one JSON
object per cell) plus an HTML ``report.html`` styled after ``tag_review.html``.

Stdlib-only. Reuses ``work_common`` and the ``work*`` modules (imported, not
copied). Use ``--sample N`` / ``--dry-run`` for a fast, network-free smoke.
"""

import argparse
import html
import json
import os
import sys
import time

# work_common + work* live at the repo root; make them importable from anywhere.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from judges import build_judge  # noqa: E402  (sibling module in eval/)

import work1  # noqa: E402  (path bootstrap must run first)
import work2  # noqa: E402
import work3  # noqa: E402
import work_common  # noqa: E402

WORK_MODULES = {"work1": work1, "work2": work2, "work3": work3}

_IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
}


# --------------------------------------------------------------------------- #
# Config + inputs
# --------------------------------------------------------------------------- #
def load_config(config_path):
    with open(config_path, encoding="utf-8") as handle:
        return json.load(handle)


def _resolve(path):
    """Resolve a config path relative to the repo root unless already absolute."""
    return path if os.path.isabs(path) else os.path.join(_REPO_ROOT, path)


def discover_images(dataset_dir):
    if not os.path.isdir(dataset_dir):
        return []
    return [
        os.path.join(dataset_dir, name)
        for name in sorted(os.listdir(dataset_dir))
        if os.path.splitext(name)[1].lower() in _IMAGE_EXTS
    ]


def load_expected_notes(dataset_dir):
    """Optional operator ground-truth notes: dataset/expected_notes.json.

    Maps image basename -> note string. Missing file -> no notes (judge is told
    "(none provided)"). This sidecar is text-only and never contains images.
    """
    path = os.path.join(dataset_dir, "expected_notes.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _dry_output(work_name, model, image_name):
    """Deterministic fake candidate output for --dry-run (no network)."""
    if work_name == "work2":
        return json.dumps({"text": [f"dry-run line from {model}", image_name]})
    if work_name == "work3":
        return json.dumps({"class": "dry-run", "confidence": 0.5})
    return f"Dry-run description of {image_name} produced by {model}."


# --------------------------------------------------------------------------- #
# Running one cell
# --------------------------------------------------------------------------- #
def run_cell(image_path, work_name, model, cfg, dry_run):
    """Run one (image, work, model) vision request, resiliently."""
    prompt = WORK_MODULES[work_name].DEFAULT_PROMPT
    image_name = os.path.basename(image_path)
    start = time.perf_counter()
    try:
        if dry_run:
            output = _dry_output(work_name, model, image_name)
        else:
            model_cfg = {"ollama_base": cfg["ollama_base"], "vision_model": model}
            output = work_common.vision_request(image_path, prompt, model_cfg)
        latency = time.perf_counter() - start
        error = None
    except Exception as exc:  # noqa: BLE001 -- resilience: never abort the run
        output = None
        latency = time.perf_counter() - start
        error = str(exc)
    return {
        "image": image_name,
        "image_path": image_path,
        "work": work_name,
        "model": model,
        "prompt": prompt,
        "output": output,
        "latency_s": round(latency, 4),
        "error": error,
    }


def judge_cell(cell, judge, judge_build_error, expected_notes):
    """Score one cell's output; images are never passed to the judge."""
    if judge_build_error is not None:
        cell.update(score=None, rationale=None, judge_error=judge_build_error)
        return cell
    if cell["error"] is not None:
        cell.update(score=None, rationale=None, judge_error=None)
        return cell
    try:
        verdict = judge.score(cell["work"], cell["output"], expected_notes)
        cell.update(score=verdict["score"], rationale=verdict["rationale"], judge_error=None)
    except Exception as exc:  # noqa: BLE001 -- resilience: never abort the run
        cell.update(score=None, rationale=None, judge_error=str(exc))
    return cell


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _mean(values):
    return sum(values) / len(values) if values else None


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct / 100.0
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def aggregate(cells, models, works):
    """Compute per-model/work means, pairwise deltas, latency pctls and ranking."""
    per_model_work = {}
    for model in models:
        per_model_work[model] = {}
        for work in works:
            scores = [
                c["score"]
                for c in cells
                if c["model"] == model and c["work"] == work and c["score"] is not None
            ]
            per_model_work[model][work] = _mean(scores)

    pairwise = {}
    for work in works:
        for i, left in enumerate(models):
            for right in models[i + 1 :]:
                lm = per_model_work[left][work]
                rm = per_model_work[right][work]
                delta = None if lm is None or rm is None else round(lm - rm, 3)
                pairwise.setdefault(work, []).append({"a": left, "b": right, "delta": delta})

    latency = {}
    for model in models:
        lats = [c["latency_s"] for c in cells if c["model"] == model and c["error"] is None]
        latency[model] = {
            "p50": _percentile(lats, 50),
            "p90": _percentile(lats, 90),
            "p95": _percentile(lats, 95),
            "n": len(lats),
        }

    ranking = []
    for model in models:
        scores = [c["score"] for c in cells if c["model"] == model and c["score"] is not None]
        ranking.append({"model": model, "mean": _mean(scores), "scored": len(scores)})
    ranking.sort(key=lambda row: (row["mean"] is not None, row["mean"] or 0), reverse=True)

    return {
        "per_model_work": per_model_work,
        "pairwise": pairwise,
        "latency": latency,
        "ranking": ranking,
    }


# --------------------------------------------------------------------------- #
# Output writers
# --------------------------------------------------------------------------- #
def write_results(cells, results_path):
    os.makedirs(os.path.dirname(results_path) or ".", exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as handle:
        for cell in cells:
            handle.write(json.dumps(cell, ensure_ascii=False) + "\n")


def _fmt(value):
    return "—" if value is None else f"{value:.2f}"


def _score_badge(score):
    if score is None:
        return '<span class="badge bna">n/a</span>'
    cls = "bad" if score <= 2 else "mid" if score == 3 else "good"
    return f'<span class="badge {cls}">{score}</span>'


def _e(value):
    return html.escape("" if value is None else str(value))


def build_report(cells, agg, models, works, meta):
    """Render the static HTML report (styled after tag_review.html)."""
    by_image = {}
    for cell in cells:
        by_image.setdefault(cell["image"], []).append(cell)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>Vision-model eval report</title>",
        "<style>",
        " :root { --keep:#1a7f37; --drop:#cf222e; --pending:#57606a; }",
        " * { box-sizing: border-box; }",
        " body { font: 13px/1.4 -apple-system, system-ui, sans-serif; margin:0;",
        "        background:#f6f8fa; color:#1f2328; }",
        " header { position:sticky; top:0; background:#fff; padding:10px 16px;",
        "          border-bottom:1px solid #d0d7de; display:flex; gap:8px;",
        "          align-items:center; flex-wrap:wrap; z-index:10; }",
        " header b { font-size:14px; }",
        " main { padding:16px; }",
        " h2 { font-size:15px; margin:24px 0 8px; }",
        " .stats { color:var(--pending); }",
        " table { border-collapse:collapse; width:100%; background:#fff;",
        "         margin-bottom:16px; }",
        " th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #eaeef2;",
        "          vertical-align:top; }",
        " th { background:#f6f8fa; font-size:12px; }",
        " td.num { text-align:right; font-variant-numeric:tabular-nums; }",
        " pre { white-space:pre-wrap; word-break:break-word; margin:0 0 4px;",
        "       max-height:180px; overflow:auto; font-family:ui-monospace, monospace;",
        "       font-size:12px; }",
        " .rat { color:var(--pending); font-style:italic; }",
        " .err { color:var(--drop); font-family:ui-monospace, monospace; }",
        " .badge { font-weight:700; padding:1px 7px; border-radius:10px; color:#fff; }",
        " .good { background:var(--keep); } .mid { background:#9a6700; }",
        " .bad { background:var(--drop); } .bna { background:var(--pending); }",
        "</style></head><body>",
        "<header><b>Vision-model eval report</b>",
        f'<span class="stats">{_e(meta["summary"])}</span></header>',
        "<main>",
    ]

    # Ranking
    parts.append("<h2>Ranking (overall mean judge score)</h2>")
    parts.append("<table><thead><tr><th>#</th><th>model</th><th>mean</th>")
    parts.append("<th>cells scored</th></tr></thead><tbody>")
    for idx, row in enumerate(agg["ranking"], 1):
        parts.append(
            f"<tr><td>{idx}</td><td>{_e(row['model'])}</td>"
            f"<td class='num'>{_fmt(row['mean'])}</td>"
            f"<td class='num'>{row['scored']}</td></tr>"
        )
    parts.append("</tbody></table>")

    # Mean per model per work
    parts.append("<h2>Mean judge score — model × work</h2>")
    parts.append("<table><thead><tr><th>model</th>")
    parts.extend(f"<th>{_e(w)}</th>" for w in works)
    parts.append("</tr></thead><tbody>")
    for model in models:
        cells_html = "".join(
            f"<td class='num'>{_fmt(agg['per_model_work'][model][w])}</td>" for w in works
        )
        parts.append(f"<tr><td>{_e(model)}</td>{cells_html}</tr>")
    parts.append("</tbody></table>")

    # Pairwise deltas
    if len(models) > 1:
        parts.append("<h2>Pairwise mean-score deltas (a − b)</h2>")
        parts.append("<table><thead><tr><th>work</th><th>a</th><th>b</th>")
        parts.append("<th>delta</th></tr></thead><tbody>")
        for work in works:
            for pair in agg["pairwise"].get(work, []):
                parts.append(
                    f"<tr><td>{_e(work)}</td><td>{_e(pair['a'])}</td>"
                    f"<td>{_e(pair['b'])}</td>"
                    f"<td class='num'>{_fmt(pair['delta'])}</td></tr>"
                )
        parts.append("</tbody></table>")

    # Latency percentiles
    parts.append("<h2>Latency percentiles (s, successful cells)</h2>")
    parts.append("<table><thead><tr><th>model</th><th>p50</th><th>p90</th>")
    parts.append("<th>p95</th><th>n</th></tr></thead><tbody>")
    for model in models:
        lat = agg["latency"][model]
        parts.append(
            f"<tr><td>{_e(model)}</td><td class='num'>{_fmt(lat['p50'])}</td>"
            f"<td class='num'>{_fmt(lat['p90'])}</td>"
            f"<td class='num'>{_fmt(lat['p95'])}</td>"
            f"<td class='num'>{lat['n']}</td></tr>"
        )
    parts.append("</tbody></table>")

    # Per-image side-by-side
    parts.append("<h2>Per-image outputs (side by side)</h2>")
    for image in sorted(by_image):
        parts.append(f"<h3>{_e(image)}</h3>")
        parts.append("<table><thead><tr><th>work</th>")
        parts.extend(f"<th>{_e(m)}</th>" for m in models)
        parts.append("</tr></thead><tbody>")
        for work in works:
            parts.append(f"<tr><td>{_e(work)}</td>")
            for model in models:
                cell = next(
                    (c for c in by_image[image] if c["work"] == work and c["model"] == model),
                    None,
                )
                parts.append(f"<td>{_cell_html(cell)}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")

    parts.append("</main></body></html>")
    return "\n".join(parts)


def _cell_html(cell):
    if cell is None:
        return "<span class='err'>(missing)</span>"
    if cell["error"] is not None:
        return f"<span class='err'>ERROR: {_e(cell['error'])}</span>"
    chunks = [f"<pre>{_e(cell['output'])}</pre>"]
    if cell["judge_error"] is not None:
        chunks.append(f"<div class='err'>judge error: {_e(cell['judge_error'])}</div>")
    else:
        chunks.append(f"<div>{_score_badge(cell['score'])} ")
        chunks.append(f"<span class='rat'>{_e(cell['rationale'])}</span></div>")
    return "".join(chunks)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        default=os.path.join(_EVAL_DIR, "eval_config.json"),
        help="path to eval_config.json (default: alongside this script)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="run only the first N images (overrides config 'sample')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="stub all model + judge calls (no network); needs no live endpoint",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    cfg = load_config(args.config)

    models = list(cfg.get("candidate_models", []))
    works = [w for w in cfg.get("works", []) if w in WORK_MODULES]
    dataset_dir = _resolve(cfg["dataset_dir"])
    results_path = _resolve(cfg["results_path"])
    report_path = _resolve(cfg["report_path"])

    sample = args.sample if args.sample is not None else cfg.get("sample")
    images = discover_images(dataset_dir)
    if args.dry_run and not images:
        count = sample if sample else 2
        images = [os.path.join(dataset_dir, f"dry-image-{i}.png") for i in range(1, count + 1)]
    if sample:
        images = images[:sample]

    expected_notes = load_expected_notes(dataset_dir)

    if not models:
        print("No candidate_models configured; nothing to do.", file=sys.stderr)
        return 1
    if not images:
        print(
            f"No images found in {dataset_dir} (and not --dry-run); nothing to do.",
            file=sys.stderr,
        )
        return 1

    judge = None
    judge_build_error = None
    try:
        judge = build_judge(cfg, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 -- record, still emit model outputs
        judge_build_error = f"judge unavailable: {exc}"

    cells = []
    for image_path in images:
        note = expected_notes.get(os.path.basename(image_path))
        for work in works:
            for model in models:
                cell = run_cell(image_path, work, model, cfg, args.dry_run)
                cells.append(judge_cell(cell, judge, judge_build_error, note))

    agg = aggregate(cells, models, works)
    summary = (
        f"{len(images)} images · {len(works)} works · {len(models)} models · "
        f"{len(cells)} cells{' · DRY RUN' if args.dry_run else ''}"
    )
    meta = {"summary": summary}

    write_results(cells, results_path)
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(build_report(cells, agg, models, works, meta))

    print(summary)
    print(f"Wrote {results_path}")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
