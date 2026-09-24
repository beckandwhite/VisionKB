# NERv3 — a growing, *typed* canonical tag registry with AKA

**Date:** 2026-08-30
**Repository:** screenshot_annotation
**Follows:** `NamedEntityRecognition.md` (NER v1, `ner.py`),
`NER_v2.md` (tag miner `work6.py` + `tag_review.py`).

## Goal

Turn the per-image free-text descriptions (`work1_generic.jsonl`
`output.answer`) into a **single, dense list of canonical tags** — one
string each, with its **occurrence count on the dataset**, its **type**, and
an **AKA table of equivalent surface forms**. The list must stay *dense* (far
shorter than the full descriptions) yet *faithful* (every tag says what it is,
how often it appeared, and what other spellings mean the same thing).

Two explicit sub-goals from the brief:

1. **Names of people** mentioned.
2. **Places.**
3. **Anything else, unclassified** — modelled as a first-class *type*, not a
   leftover.

The hard part is (a) collapsing the surface noise — `Wittek, Adam`,
`ADAM WITTEK`, `Adam Wittek`, `Wittek Adam` — into **one** entity, and
(b) the **semantic AKA** that a string operation *cannot* derive:
`"Bravo"` ≈ `"Adam Wittek"` (his team). The semantic AKA is **curated
knowledge that must accumulate over time**, so the artifact is a persistent,
append-only registry, not a per-run dump.

## Decisions (locked)

| # | Decision | Choice |
|---|---|---|
| 1 | Registry scope | **Per-environment** `.workspace/<env>/canonical_tags.json`. |
| 2 | Alias approval | **`orthographic` auto** (case / diacritic / HU-vs-EN word order); **`aka` human-confirmed**. |
| 3 | "Unclassified" / types | **7-type taxonomy** (Option A), engine-recommended, human-overridable. `Other` is the universal catch-all. |
| 4 | AKA relations | **Relations supported**: `alias.relation ∈ {identity, member, team, aka, part-of}`. |
| 5 | Deliverable | This doc + a runnable `work7.py` prototype + a `tag_review.py` `entities` mode. |

> **Stored tradeoff (conscious):** per-env-only means curated AKA is gitignored
> (`.workspace/` is in `.gitignore:2`) and lost on `decomm`. **Opt-in
> mitigation:** `work7 build --backup` copies just the `kind:aka` subset to a
> git-tracked `aliases.curated.json` at the repo root so knowledge can be
> versioned. Not created unless asked.

## Background: what already exists

- `named_entities.jsonl` (output of `ner.py`): `{type, display, variants,
  count}` — 4121 entities, **type field unreliable** (`YouTube`/`Grafana`/
  `claude`/`inbox` are `Person`; `Microsoft`/`macOS`/`Datadog`/`Wi-Fi`/`Kyma`
  are miscategorised). Its `variants` already collapse the **trivial** AKA
  (the "Wittek Adam" case) via `fold()` + `norm_key()` (`ner.py:86`).
- `work6_tag_candidates.jsonl`: frequent 1/2/3-grams — the **scene/topic**
  vocabulary (the "anything else" material).
- `config.template.json` `TAG_LIST`: a hand-curated hyphenated scene taxonomy
  (`conference-call`, `coding-dev`, …) — the seed `Scene` set.

`ner.py` imports `spacy` at module top, so it **cannot** be imported by a
stdlib-only 3.9 producer. `work7` therefore **replicates** `fold` /
`norm_key` / `pick_display` locally (the orthographic-merge logic already
exists; we copy it, not re-derive it).

## The two-time split (the architecture)

The brief itself splits the job; the system honours it.

| Phase | Question | Actor | Artifact |
|---|---|---|---|
| **Population** | "What *strings* can be tags?" | engine | `named_entities.jsonl` + `work6` (done) |
| **Consolidation** | "Collapse surfaces into **one** tag each + **type** it" | engine auto + human | **`canonical_tags.json`** (new, per-env) |
| **Tagging** | "For *this* screenshot, which canonical tags apply?" | resolver | per-image `tags[]` (existing field) |

```
 named_entities.jsonl + work6_tag_candidates.jsonl      (population — done; type NOT trusted)
        │
 [work7 build]  ortho-merge variants (fold)  +  3 AKA proposers  +  7-type engine
        │        → canonical_tags.json  (new/!merged entries are status: "proposed")
 [tag_review entities]  human confirms/denies semantic AKA  →  approved appended
        │                  (kind:aka, status:active)   ← the append-only knowledge layer
 [work7 resolve]  folded alias→canonical index  +  unmatched mentions → unresolved.log
        │                 (unresolved = next run's alias proposals = the growth feed)
 per-image tags[]    (the existing _annotations.jsonl field)
```

## Data model — `canonical_tags.json`

One JSON **list**; one element per canonical entity. Written atomically
(`tempfile` + `os.replace`), per-env, mirroring `work5.write_result`
(`work5.py:71`).

```jsonc
{
    "id": "person.adam-wittek",        // stable, slug(canonical); env-independent
    "type": "Person",                  // from the 7-type engine (recommended, overridable)
    "confidence": 0.9,                // type confidence; human may override
    "canonical": "Adam Wittek",        // the ONE string we tag with
    "aliases": [
      {"form": "Wittek, Adam", "kind": "orthographic", "relation": "identity", "count": 27},  // AUTO
      {"form": "ADAM WITTEK",  "kind": "orthographic", "relation": "identity", "count": 3},    // AUTO
      {"form": "Bravo", "kind": "aka", "by": "human", "relation": "member",    "count": 9,    // CURATED
       "note": "team Bravo = Adam Wittek", "added": "2026-08-29"}
    ],
    "count": 40,                      // Σ alias counts = "occurrence on the dataset"
    "status": "active"                // active | proposed | deprecated
}
```

Load-bearing fields:
- **`kind`** — `orthographic` (auto, zero risk) vs **`aka`** (curated knowledge;
  the "Bravo = Adam Wittek" insight). This is the part that *grows over time*.
- **`relation`** — default `identity`; `member`/`team`/`aka`/`part-of` let the
  resolver surface *both* `group.bravo` and `person.adam-wittek` instead of a
  forced wrong merge. `member` is what your "Bravo" case really is.
- **`count`** — aggregated occurrence per alias, so the list is dense *and*
  faithful (what it is + how often).
- **`status`** — `active | proposed | deprecated`; new engine proposals land as
  `proposed`, human `apply` promotes them to `active`.
- **`by` / `added` / `note`** — provenance for the human-curated layer.

## The 7-type engine (point 3 — "anything else")

Because the source `type` field is unreliable, the engine **re-derives** a type
from signal and emits `(type, confidence)`; a human overrides in the harness.
Unknowns land in `Other` (your accepted universal bucket) and can be promoted
later — "subject for further engineering" stays in place.

`Person | Place | Group | Product | Scene | Concept | Other`

| Type | Signal (heuristic) |
|---|---|
| **Person** | name-list / list-context in the source (`ner.py` list gate); capitalised given-name tokens. Keep — high precision. |
| **Place** | place wordlist (`Hungary`, `Budapest`, `Frankfurt`, `Israel`, `Europe`, …); office names. |
| **Group** | `Team X`, `X Squad`, `X daily`, Jira `X → Y` mapping **left** side; `TeamBravo`. This is your "Bravo". |
| **Product** | ORG adjacent to UI words (`launchpad`, `IDE`, `server`, `chrome tab`, `window`) or in a software list (`SAP`, `VSCode`, `Datadog`, `Kyma`, `Grafana`, `Jira`, `Confluence`, `Terraform`, `ArgoCD`, …). Splits *SAP* from *Team Bravo*. |
| **Scene** | hyphenated style matching `config.template.json` `TAG_LIST`; `work6` 2/3-gram candidates (`conference-call`, `coding-dev`, `data-visualization`). |
| **Concept** | abbreviations / topics (`DevOps`, `CDC`, `cPro`, `BTP`, `GitOps`). |
| **Other** | catch-all — anything the above doesn't claim. Universal bucket. |

The engine scores an entity against each type and keeps the top as the
recommendation with a confidence = top/sum. It is **additive and
overridable** — it never deletes.

## `work7.py` spec (mirrors `work5.py` / `work6.py`)

`import config_loader`; `resolve_environment(env)`; **stdlib-only, Python 3.9**
(no `spacy`); atomic `tempfile` + `os.replace` writes. Subcommands:

### `build`
Ingest `named_entities.jsonl` (re-derive type; **ignore** its `type`) and
`work6_tag_candidates.jsonl`; **ortho-merge** variants via `fold` /
`norm_key` (copied from `ner.py:86`); emit `canonical_tags.json` with
`proposed` AKA merge-candidates from the three AKA proposers below.
Flags: `-env`, `--input` (named_entities), `--candidates` (work6),
`--out` (canonical_tags.json), `--min-count`, `--backup`.

### Three AKA proposers (cheap → expensive; auto-*propose*, human-*confirm*)
1. **Co-occurrence** — two surfaces recur in the same answers → propose link
   (`Bravo` & `Wittek` co-occur).
2. **Fuzzy** — edit distance / transposition (`Wittek`↔`Witek`), token-set
   Jaccard on the folded form.
3. **Structural** — `"X – Adam"`, role suffix `"Wittek, Adam – SAP Team Lead"`,
   Jira `"Bravo → Barkuni"` mapping targets.

**Trust rule:** `orthographic` aliases apply **automatically**; `aka` links are
**human-confirmed** via the review harness. This is the "auto orthogonal, human
aka" decision and keeps the growing layer trusted.

### `resolve`
Load the registry, build a **folded alias→canonical index**, and for each
input image's raw mentions emit `tags[]` of canonicals plus `unresolved.log`
(unmatched mentions → next run's alias proposals = the growth feed).
Flags: `--input` (work1_generic.jsonl or a source-key list), `--out`.

### `apply`
Read an approved-decisions JSON (from `tag_review.py entities`) and fold it
back into `canonical_tags.json` (append-only; promote `proposed` →
`active`, mark denials `deprecated`).

## Files touched

| File | Action |
|---|---|
| `Plans/NERv3.md` | This design. |
| `work7.py` | New — build / resolve / apply; stdlib; atomic writes. |
| `tag_review.py` | Extend — `entities` subcommand + HTML mode (keep/drop/confirm-aka), Export → `.entity.decisions.json` → `work7 apply`. |
| `config.template.json`, `.workspace/config.json` | Add `work7` entry (`scope:"dataset"`, `result_file:"canonical_tags.json"`, `enabled:false`) + `aliases.curated.json` artifact key. |
| `.gitignore` | Add `*.entity.decisions.json`; `unresolved.log`. |
| `ner.py` | **Reuse** `fold`/`norm_key`/`pick_display` — copied locally, no change. |

## Verification
1. `work7 build -env <env>` → `canonical_tags.json`: is **Adam Wittek** one
   entity with `Wittek, Adam`/`ADAM WITTEK` auto-merged and a **proposed**
   `Bravo`(`member`) alias?
2. `work7 resolve` on real answers → mentions map to canonicals; `unresolved.log`
   entries look worth promoting.
3. Spot-check 7-type output vs the known-bad source types (`YouTube`/`Grafana`/
   `claude` must NOT stay `Person`; `SAP` must be `Product`, `Team Bravo`
   `Group`).
4. `tag_review.py entities` openable; Export → `work7 apply` round-trips.

## Known limitations
- The 7-type engine is heuristic; `confidence` surfaces its uncertainty and a
   human override is always available.
- Co-occurrence is necessary-not-sufficient; two unrelated surfaces can recur
   together — hence `aka` is *proposed*, never auto-applied.
- Per-env registry duplicates the entity *set* across envs; only counts differ.
   A future `--global` could share the entity catalogue while keeping per-env
   counts.
- Hungarian-only person names not in any list remain in the source's regex list
   gate; prose-only names rely on `Product`/`Group`/`Concept` heuristics.
