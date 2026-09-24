# Quality Assurance & CI/CD — High-Level Plan

**Date:** 2026-09-24
**Repository:** screenshot_annotation
**Status:** Planning (kickoff). Track A is groomed; Tracks B & C are high-level, for further grooming.

## Goal

Raise quality assurance from "runs on my Mac" to an automated, security-scanned
build pipeline that also **measures vision-model quality** — so we can answer
*"how much better is model X than model Y for this pipeline?"* with data, not
vibes.

Three independent tracks, sequenced so each ships value on its own:

| Track | What | Runner needed | Groomed? | Workitem |
|---|---|---|---|---|
| **A. Baseline CI + supply-chain security** | Lint, SAST, secret scan, CodeQL, Dependabot, GHA hardening | GitHub-hosted | ✅ ready | [002](../Issues/002-baseline-ci.md), [003](../Issues/003-security-supply-chain.md) |
| **B. Private self-hosted runner** | Native macOS runner that can reach Ollama for QA jobs | self-hosted | ✅ ready | [004](../Issues/004-private-runner.md) |
| **C. Vision-model eval (LLM-as-judge)** | Compare models over a fixed image set, scored by a judge model, HTML report | local first, then B | ✅ ready | [005](../Issues/005-model-eval-harness.md) |

Sequencing: **A first** (cheap, no hardware, immediate security value) → **C as
a local script** (proves the QA value with no infra) → **B** (moves C into CI on
real hardware). B and C can be groomed in parallel; C's harness is designed to
run locally *or* on B's runner.

---

## Current state (why the plan looks the way it does)

- **Core runtime is stdlib-only Python.** `backend.py`, `work1`–`work7`,
  `tracker.py`, `config_loader.py` import only the standard library
  (`urllib`, `base64`, `json`, `os`, `time`). The `.venv` (spacy, pandas,
  numpy) is NER experimentation, not the core runtime.
  → **Implication:** classic dependency-vulnerability scanning has almost no
    runtime surface. Dependabot's real value here is **GitHub Actions version
    pinning** + any *dev* tooling we add. Security value comes from **SAST on
    our own code** (bandit/semgrep/CodeQL) and **secret scanning**, not `pip audit`.

- **Vision inference is Ollama over HTTP.** `work_common.vision_request()`
  (`work_common.py:27`) POSTs base64 images to `config["ollama_base"]`
  (`/api/generate`) with a configurable `config["vision_model"]`. Endpoint
  currently points at a LAN Mac (`http://D72nq7w67l.local:11434`).
  → **Implication:** model comparison is a *config swap + re-run + score* loop.
    The pipeline already parameterizes the model, so the eval harness mostly
    needs a runner, a judge, and a report.

- **Image handling is macOS-bound.** The pipeline shells out to `sips`
  (macOS-only) for HEIC→JPEG and thumbnails.
  → **Implication (decided):** keep it Mac-only; use a **native macOS**
    self-hosted runner so `sips` exists. A Linux Docker container would break
    `sips`. See Track B topology options.

- **No CI, no Dockerfile, no tests, no Dependabot** — greenfield.

---

## Decisions (locked at kickoff)

| # | Decision | Choice |
|---|---|---|
| 1 | First shippable slice | **Baseline CI + security** (Track A), hosted runners. |
| 2 | Model scoring method | **LLM-as-judge** (a stronger reference model grades candidates per rubric). |
| 3 | Image-ops portability | **Keep Mac-only**; native macOS self-hosted runner (`sips` stays). |
| 4 | Runner topology | **Option 2**: native macOS runner → Ollama on a separate LAN Mac (Option 1, co-located, is the documented fallback). |
| 5 | Eval dataset privacy | Eval images are **sanitized/synthetic or stored outside git** — never commit personal screenshots. |
| 6 | Judge implementation | **Both, config-selectable** — local Ollama judge *and* frontier API judge behind one interface; pick per run. |
| 7 | Repo visibility | **Public.** Self-hosted runner requires hard fork-PR gating; secret-scanning + eval-dataset exclusion are critical, not optional. |

---

## Track B — runner topology options (decide later)

All reach the same Ollama; they differ in isolation, security exposure, and
whether `sips` survives. Ranked by fit for *this* repo.

**Option 1 — Native macOS runner, Ollama on the same box (localhost).** *(Recommended)*
Runner process runs directly on a Mac; Ollama listens on `127.0.0.1:11434`.
`sips` works. No network exposure of Ollama. Fastest. Downside: runner + model
share one machine's resources; runner tied to that hardware.

**Option 2 — Native macOS runner, Ollama on another LAN Mac.** *(Your stated default)*
Runner Mac calls `http://<host>.local:11434` over the LAN. `sips` works. Splits
load across two boxes. Downside: Ollama must listen on the LAN interface
(bind-address + firewall + ideally a Tailscale/WireGuard mesh, never the open
internet); CI shares that model server's queue with interactive use.

**Option 3 — Docker (Linux) runner on a Mac, calling host Ollama via
`host.docker.internal:11434`.** Strong job isolation. **Breaks `sips`** — the
container is Linux, so any job touching HEIC/thumbnails fails unless we add a
Linux image backend (deferred by Decision 3). Use only for jobs that never
touch image conversion.

**Option 4 — GitHub-hosted ephemeral runner + tunnel to LAN Ollama.**
No self-hosted infra to maintain. Requires exposing Ollama through a tunnel
(Tailscale/cloudflared) with auth — largest attack surface, and hosted runners
can't run `sips`-dependent steps. Best only for the pure-text judge/report
steps if we ever split them out.

**Security rule for all self-hosted options:** self-hosted runners must **never**
execute untrusted fork-PR code (RCE against your LAN). Keep the repo private, or
gate `pull_request` from forks behind manual approval and restrict the
self-hosted job to `push`/`workflow_dispatch` on trusted branches. Detailed in
[004](../Issues/004-private-runner.md).

---

## Track C — vision-model eval, shape of it (detail in [005](../Issues/005-model-eval-harness.md))

```
 fixed image set (sanitized)          candidate models (config list)
        │                                     │
        ▼                                     ▼
   for each (image × work-prompt × model):  vision_request() ──► raw output + latency
        │
        ▼
   judge model scores each output against a per-work rubric ──► {score, rationale}
        │
        ▼
   aggregate ──► HTML report (per-model scores, deltas, latency, throughput)
                 + machine-readable results.jsonl
```

Reuses `work_common.ollama_post_json`. Judge can be a larger local Ollama model
(no data leaves the LAN) or a frontier API model (stronger, but sends *derived
text* off-box — never the images). Report style mirrors the existing
`tag_review.html` artifacts.

---

## Open questions for grooming

Resolved at grooming (2026-09-24): judge = both, config-selectable (Q2 → Decision 6);
runner = Option 2 native macOS → LAN Ollama (Q4 → Decision 4); repo = public
(Q5 → Decision 7); gating starts informational (Q6, report-only until scores are trusted).

Still open — runtime/config choices, not blockers for implementing 004/005:

1. **Candidate model shortlist** for the first comparison (e.g. current
   `muse-glimmer:30b-mlx` vs a smaller/faster model vs a larger one). This is
   `eval_config.json` data, not code — set it when the runner exists.
2. **Judge model id** for each judge type (which local Ollama model; which API model).
3. **Eval set curation**: default is ~25 sanitized/synthetic images, operator-curated.
   Confirm count and who supplies the per-image expected-answer notes the rubrics lean on.
