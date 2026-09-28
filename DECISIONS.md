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
- **2026-08-20 · Source images are read-only from
  `~/Library/Mobile Documents/com~apple~CloudDocs/Screenshots/`; the working dir
  is the git checkout.** → `implementation.md §0`.
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
  the 30B model for cluster representatives.** The active vision model
  `muse-glimmer:30b-mlx` runs ~90 s/img (~50 h for 2028 images), which forces
  dedup + tiering. Ollama is single-stream; Python "concurrency" does not speed
  up vision. → `implementation.md §1/§3`.
- **2026-08-20 · Embedding pre-pass with `nomic-embed-text`; hierarchical
  clustering, `min_cluster_size=3`; loners fold into "misc".** → `implementation.md §2`.
- **2026-08-25 · `_tracker.json` is the single source of truth for progress +
  per-file telemetry; the old `telemetry.log` is retired.** `backend.py` is
  incremental (re-ingests only mtime-newer records; adopts on-disk thumbnails
  without re-running `sips`). → `implementation.md §3`, `WebUI-1.0-plan.md`.

## Concurrency & environment safety

- **2026-08-25 · `.pipeline.lock` enforces serial, single-writer execution.** It
  (a) prevents race conditions on shared state (`config.json`, DBs), (b) guards
  against accidental double-triggers processing the same file, and (c) records a
  PID for stale-run detection. Writer tasks acquire it and release on completion.
  → `lock.md`.
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
- **Image display = placeholder tile + `file://` "open original" link,**
  degrading to a copyable mono path if the browser blocks `file://`.
- **README gets a short `## WebUI` run section** after building.

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

## Image format handling (2026-09-25, #33 — resolved)

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
- **Scanning strategy for a stdlib-only runtime (#30):** the high-value signal is
  SAST (bandit/CodeQL) + secret-scanning + GitHub-Actions hardening — **not**
  dependency-CVE scanning. The expected bandit finding on `work_common`'s
  plain-HTTP `urllib` call is an **accepted, documented** finding (not broadly
  suppressed); `pip-audit` is deferred until the runtime takes a third-party dep
  (e.g. Pillow from #33).

## Self-hosted runner & secrets policy (2026-09-24, `SECURITY.md`, #31)

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

## #21 — Improve readability: 8.1 WebUI tab navigation shell

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `refreshAll()` guards **both** `renderBacklog` and `loadTimeline` (the full Search data path), not just the timeline fetch.
  **Why:** Both calls fetch API endpoints and update DOM nodes that live inside `#tab-search`; skipping both when the tab is inactive avoids wasted network requests while still updating `#last-updated` each poll tick.
  **Alternatives considered:** Guard only `loadTimeline`; chose the broader guard as more consistent with the stated intent.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `activateTab` sets and clears both the `active` CSS class and the `hidden` class on `.tab-view` sections.
  **Why:** Initial HTML uses `active` on `#tab-search` (no `hidden`); after the first `activateTab` call the function normalises all views to `active`/`hidden` semantics consistently.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Stub comment text refers to the filling issue (e.g. `/* filled by issue 8.3 */`).
  **Why:** Makes the dependency visible at a glance; purely informational, zero functional impact.

## #23 — Improve readability: 8.3 Tag Forge tab

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `initTagforge()` calls `renderTags()` on every invocation (no idempotency guard).
  **Why:** `renderTags()` clears all mount points before re-rendering (`innerHTML = ""`), so repeated calls are safe and give fresh data. A one-shot guard would stale the view if the user navigates away and back.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `populateTagFilter()` function definition left in place; only the call in `renderTags()` was removed.
  **Why:** The spec says "Remove the dead `populateTagFilter` call" — the call site only. Deleting the definition would touch code outside the listed scope; left for a future cleanup pass.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Tag-click handlers call `activateTab("search")` then `loadTimeline(true)` without `scrollIntoView`.
  **Why:** `activateTab` switches the visible tab and the search content is at the top of that view; a `scrollIntoView` would be redundant and could scroll past the tab bar.

## #5 — Same telemetry for work2 as for work1

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Added `/api/works` endpoint to expose enabled per_source work names to the frontend.
  **Why:** The frontend needs a config-driven list of works to loop over; embedding it in the overview response would conflate two concerns. A dedicated endpoint is the minimal addition.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Chart heading label changed from "processing time" to "per-file metric" for all works (not just work2).
  **Why:** Work2 shows lines-extracted count, not time; a generic label is accurate for all works and avoids a branch. The bar tooltip still shows the exact value with its unit.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `renderBacklog` fetches `/api/works` on every poll tick (every 5 s).
  **Why:** The works list is tiny and cheap; caching it would add state. No perf concern at this scale.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Panel IDs use the work name directly (e.g. `work2-backlog`) rather than a "Worker N" label.
  **Why:** Work names are stable identifiers from config; display labels (e.g. "Worker 2") are derived from them at render time via a regex replace. This keeps IDs stable if display labels ever change.

## #22 — Improve readability: 8.2 Search tab split-view

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Mobile thumbnail bumped to 120×90 px (75% of 160×120) rather than the strict proportional value (~137×103).
  **Why:** 75% gives clean numbers and is close enough to the original ratio (82/96 ≈ 85%).

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `#detail-split` shown with `style.display = "grid"` rather than toggling a CSS class.
  **Why:** The overlay grid layout requires `display:grid`; toggling `hidden` (which sets `display:none !important`) would conflict. Inline style override is the simplest approach.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Image starts loading immediately when `openRecord` is called, before the `/api/record` fetch completes.
  **Why:** Image and record metadata are independent; loading them in parallel shaves perceived latency.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `fileLink` / `openPanel` dead code left in place — only the call sites and `openOriginal`/`closeOriginal` were removed.
  **Why:** `fileLink` is no longer called from `openRecord`. Deleting it was not listed as a step; left for a cleanup pass.

## #25 — Improve readability: 8.5a Setup tab (read-only config viewer)

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `_json_safe` uses `os.PathLike` (not `Path`) for the isinstance check.
  **Why:** `os` is already imported; `os.PathLike` covers all path-like objects without needing `from pathlib import PurePath`.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `initSetup` is not idempotency-guarded — it re-fetches and re-renders on every tab visit.
  **Why:** Config rarely changes; showing fresh data on each visit is more correct than a stale cache. The fetch is cheap (in-process dict copy).

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `setup.js` defines its own `_escHtml` rather than reusing `esc` from `app.js`.
  **Why:** `setup.js` is loaded before `app.js`; `esc` is not yet defined at definition time. When `initSetup` is called, `esc` is available, but a local copy is safer and keeps the module self-contained.

## #24 — Improve readability: 8.4 Telemetry & Logs tab

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · Issue spec refers to `#worker1-backlog` / `#worker4-backlog` sections, but those static sections no longer exist; issue #5 replaced them with a single `<div id="backlog-panels">` populated dynamically by `createBacklogPanel()`.
  **Why:** Issue #5 was implemented before #24; the spec was written when the static sections still existed. Moved `#backlog-panels` container to `#tab-telemetry` instead, which has the same visible effect — backlog charts appear under Telemetry & Logs.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `refreshAll()` now gates `renderBacklog()` + `renderErrors()` to Telemetry tab active, and `loadTimeline()` to Search tab active (two separate guards, not one combined guard).
  **Why:** The spec says "gate polling to the active tab" — backlog and timeline each live in a different tab, so each needs its own guard. An `await Promise.all(jobs)` is skipped entirely when both tabs are inactive, still updating the timestamp.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `renderErrors()` is defined in `telemetry.js` (not `app.js`) to keep it collocated with `initTelemetry()`.
  **Why:** The spec says to "add error-table render" in app.js/telemetry.js and describes `initTelemetry()` calling `renderErrors()`. Placing `renderErrors` in `telemetry.js` keeps the telemetry module self-contained; `refreshAll()` in `app.js` calls `renderErrors()` through the same `typeof fn === "function"` pattern would work, but since `renderErrors` is always available via `telemetry.js`, a direct call is simpler.

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · `telemetry.js` defines `_escTel` (local HTML escaper) rather than reusing `esc` from `app.js`.
  **Why:** Same reason as `setup.js` / `_escHtml`: `telemetry.js` loads before `app.js`. Self-contained module is safer.

## #4 — Feedback tab: static project page

- **2026-09-28** · executor: size:S (Claude Sonnet 4.6) · No judgment calls or deviations from the spec. All steps followed exactly as written.
  **Why:** Spec was unambiguous; all three files (`feedback.js`, `style.css`, `.github/ISSUE_TEMPLATE/bug_report.md`) were created/modified exactly as specified. No backend changes, no other files touched.
