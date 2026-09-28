# Decision Log

Single source of truth for decisions on this project. Two parts:

1. **Decision register** — durable project / architecture / policy decisions,
   consolidated from the planning docs, issues, and security policy, dated where
   known.
2. **Autonomous work-item log** — append-only per-issue record written by
   autonomous runs. See [`Plans/autonomous-sizing.md`](Plans/autonomous-sizing.md)
   for the S/M/L sizing scheme and the self-routing rule that points here.

> **Consolidated 2026-09-28** from `Plans/*.md`, `Issues/*.md`, `SECURITY.md`,
> `README.md`, `recommendedHW.md`. Each source still carries its decisions inline
> for local context and points here as the canonical log. Raw session transcripts
> (`Plans/session-ses_*.md`) were intentionally **not** mined — they are process
> logs, not decision records.

---

# Part 1 — Decision register

Dates are the decision's own stated date where the doc gives one, otherwise the
date the doc entered git. `→` names the source doc.

## Platform & runtime

- **2026-08-20 · Python 3.9, stdlib-only for pipeline/stage scripts.** No `match`,
  no PEP 604 `X | Y` at runtime, no runtime `list[...]`/`dict[...]` generics.
  → `implementation.md §4`, `README.md`, `WebUI-1.0-plan.md`.
- **2026-08-20 · Pipeline code lives in the git repo; runtime data and generated
  outputs are isolated under `.workspace/<env>/`.** Root `exports/` is not used.
  Git checkout stays the code source of truth. → `implementation.md §0/§4`.
- **2026-08-20 · Model config lives in the active environment's `config.json`,**
  loaded by `config_loader.py`; all paths come from there — never hardcoded in
  stage modules. → `implementation.md §0/§3`.
- **2026-08-20 · Image ops use `sips` / `ffmpeg` only — no ImageMagick.**
  tesseract is English-only; non-English OCR is a known, non-blocking limitation.
  → `implementation.md §4`.

## Data / pipeline

- **2026-08-20 · Dedup first, before spending vision calls.** exact `sha256` +
  near-dup perceptual hash / embedding cosine < 0.98; collapse `..._n 1/2` dupes.
  Run vision on the deduplicated set (~1200–1600), not all 2028. → `implementation.md §1/§2`.
- **2026-08-20 · Tiered models: cheap/small model for the volume pass, reserve
  the 30B model for cluster representatives.** Ollama is single-stream; Python
  "concurrency" does not speed up vision. → `implementation.md §1/§3`.
- **2026-08-20 · Embedding pre-pass with `nomic-embed-text`; hierarchical
  clustering, `min_cluster_size=3`; loners fold into "misc".** → `implementation.md §2`.
- **2026-08-25 · `_tracker.json` is the single source of truth for progress +
  per-file telemetry; the old `telemetry.log` is retired.** `backend.py` is
  incremental (re-ingests only mtime-newer records; adopts on-disk thumbnails
  without re-running `sips`). → `implementation.md §3`, `WebUI-1.0-plan.md`.

## Concurrency & environment safety

- **2026-08-25 · `.pipeline.lock` enforces serial execution.** Writer tasks
  acquire/release; a PID is recorded for stale-run detection; protects shared
  state (`config.json`, DBs) from concurrent writers. → `lock.md`.
- **2026-08-28 · `decomm`/`reset` refuse to run while an environment's
  `.pipeline.lock` is actively held; the default workspace is forbidden as a
  decomm target.** → `README.md`.

## WebUI (2026-08-20, `WebUI-1.0-plan.md`)

- **Stack = stdlib `http.server` backend + vanilla JS/HTML/CSS.** Zero deps, no
  build step, 3.9-safe.
- **Progress model = funnel of pipeline stages**, each a % of `TOTAL` (tracker
  `total_images`); stages read live from their source files fresh per request.
- **ETA = `avg_latency` over `ok` tracker rows × remaining**, with a latency
  sparkline.
- **Original layout = one scrolling page (Backlog → Timeline → Tags).**
  *Superseded 2026-09-27 by the 4-tab UI* — board #8 / #21 (Search · Tag Forge ·
  Telemetry & Logs · Setup).

## NER / canonical tags (2026-08-30, `NERv3.md`)

- **Registry is per-environment** `.workspace/<env>/canonical_tags.json`.
- **Alias approval: `orthographic` auto** (case / diacritic / HU-vs-EN word
  order); **`aka` human-confirmed.**
- **7-type taxonomy** (engine-recommended, human-overridable); `Other` is the
  universal catch-all.
- **Alias relations** ∈ `{identity, member, team, aka, part-of}`.
- **Conscious tradeoff:** per-env registry means curated AKA is gitignored and
  lost on `decomm`; opt-in `work7 build --backup` copies the `kind:aka` subset to
  a git-tracked `aliases.curated.json` (not created unless asked).

## Image format handling (2026-09-25, `Issues/001` — resolved)

- **HEIC/HEIF → JPEG transcoded in memory via `pillow-heif`; no files written to
  disk.** JPEG/PNG pass through unchanged.
- **0-byte source files are rejected up front** with a truthful message instead
  of an opaque HTTP 400.
- **`pillow` + `pillow-heif` added to `requirements.txt`** — the first runtime
  third-party deps (previously stdlib-only runtime).
- **Deferred:** classifying permanent vs transient errors so permanent failures
  stop being re-queued by the retry loop.

## QA / CI / security — locked at kickoff (2026-09-24, `Plans/QualityAssurance-CI.md`)

Numbering preserved so existing "Decision N" references resolve here.

1. **First shippable slice = Baseline CI + security** (Track A), hosted runners.
2. **Model scoring method = LLM-as-judge** (stronger reference model grades
   candidates per rubric).
3. **Image-ops portability = keep Mac-only**; native macOS self-hosted runner so
   `sips` stays (a Linux/Docker runner would break it).
4. **Runner topology = Option 2** — native macOS runner → Ollama on a separate
   LAN Mac; co-located Option 1 (`localhost`) is the documented fallback.
5. **Eval dataset privacy** = sanitized/synthetic or stored outside git; never
   commit personal screenshots.
6. **Judge implementation = both, config-selectable** — local Ollama judge *and*
   frontier API judge behind one interface; judges never receive images (derived
   text only).
7. **Repo visibility = public** → self-hosted runner requires hard fork-PR
   gating; secret-scanning + eval-dataset exclusion are critical.
- **Grooming (2026-09-24):** CI quality gating starts **informational**
  (report-only) until scores are trusted.

## Self-hosted runner & secrets policy (2026-09-24, `SECURITY.md`, `Issues/004`)

- **Self-hosted jobs trigger only on `workflow_dispatch` and `push` to `main`** —
  never fork `pull_request`, never `pull_request_target`. "Require approval for
  all external contributors" is enabled. PR-time lint/SAST/secret-scan stay on
  GitHub-hosted runners.
- **Runner = dedicated non-admin user, ephemeral working dir**, narrow label
  `self-hosted-macos-ollama`; secrets not persisted between jobs.
- **Ollama endpoint bound to loopback + private LAN/mesh only**, firewalled from
  the WAN, reached over LAN or mesh VPN (Tailscale/WireGuard) — never the open
  internet. `OLLAMA_BASE` / API keys come from repo secrets or runner env.

## Autonomous execution & sizing (2026-09-28, board #27, `Plans/autonomous-sizing.md`)

- **Work items in "Ready to be picked up by AI" carry a T-shirt size** that maps
  to the model, and therefore the machine, that should execute them:
  `size:S` = Gemma 4 12B (Mac mini 16GB) · `size:M` = Qwen3 ~27–30B (48GB) ·
  `size:L` = cloud/frontier (>128K context).
- **Size is represented as a GitHub label** (`size:S|M|L`) so an autonomous
  executor can read it from the issue and self-route.
- **Autonomous runs record judgment calls, assumptions, and deviations in this
  file** (Part 2), keyed by issue number; ambiguity → STOP and log rather than guess.

---

# Part 2 — Autonomous work-item log

Append-only. One entry per **judgment call, assumption, or deviation** an
autonomous run makes; routine unambiguous steps need no entry. Group under a
`## #<issue> — <title>` heading, newest at the bottom of that section.

### Entry format

```
## #<issue> — <short title>
- **YYYY-MM-DD** · executor: <size> <model> · <the decision or deviation, one line>.
  **Why:** <reason>. **Alternatives considered:** <if any>.
```

---

<!-- Entries below this line. -->
