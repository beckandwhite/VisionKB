# Tag Miner (n-grams)

**Date:** 2026-08-29
**Repository:** screenshot_annotation
**Follows:** `Plans/NamedEntityRecognition.md` (NER v1).

## Goal
Populate TAGs by mining frequent **word, word-pair, and word-triple**
sequences (1/2/3-grams) out of the free-text `output.answer` field of
`work1_generic.jsonl` (the work1 vision descriptions). The point is *not*
to auto-assign tags; it is to produce a frequency-ranked candidate list that a
human **cherry-picks** to extend the canonical `TAG_LIST` in
`config.template.json`. Multi-word entries capture multiword expressions
(`boardroom pitch`, `conference call`, `desk setup`) that single words miss.

This is deliberately simple, deterministic, and stdlib-only: it is the cheap,
noise-visible precursor to any LLM/entity-based tagging, and it is the input to
manual curation rather than a final taxonomy.

## Relationship to NER v1
`ner.py` extracts proper nouns (People / Org / Location). This miner extracts
**content phrases** regardless of case or diacritics — the opposite signal.
Both read the same source field and share the same fold/clean helpers
(`unicodedata` NFD, drop `Mn`, lowercase), so a phrase like `boardroom` and a
proper noun like `Boardroom` count together. This miner is **not** wired into
`ner.py` (which imports `spacy` at module load); it reimplements the small
loader so it stays stdlib-only and runnable under system Python 3.9.

## Pipeline
1. **Load.** Reuse `ner.py`'s `load_answers(path)`: read each JSONL line, yield
   `record["output"]["answer"]` when present; skip null-error records.
2. **Split.** Tokenize each answer with a diacritic-aware word regex
   (`[A-Za-zÀ-ÖØ-öø-ÿ]+('−—-|…)*`), i.e. words only, no punctuation glued on.
3. **Fold + clean.** Per token: fold case + diacritics (so `macOS`/`Sap` →
   `macos`/`sap`); drop empty, pure-digit, single-char, and stopword tokens.
   N-grams are then formed over the **stopword-filtered token stream**, so a
   "word pair next to each other" means adjacent in the cleaned stream
   (`foo bar baz` → 1-grams `foo,bar,baz`; 2-grams `foo bar, bar baz`;
   3-grams `foo bar baz`).
4. **Count.** One `Counter` per `n`; each n-gram joins its folded tokens with a
   single space as the `ngram` string.
5. **Flatten + rank.** Emit one record per n-gram as
   `{"ngram", "count", "n"}` where `n` is 1/2/3. Filter by `--min-count`
   (default 2, to drop one-off noise) and sort by `(-count, n, ngram.lower())`.
   The single `n` field flags the type inside **one flat array**, so a reviewer
   can slice by type (`n==1`, `n==2`, `n==3`) in one pass.

## Output
One JSONL file, `.workspace/work6_tag_candidates.jsonl` (the env dir, same
place as `duplicatefinder.jsonl`), one object per line:
```
{"ngram": "boardroom", "count": 12, "n": 1}
{"ngram": "boardroom pitch", "count": 3, "n": 2}
{"ngram": "conference call recording", "count": 2, "n": 3}
```
The existing downstream consumer `tags_index.json` (read by `frontend.py`
`load_tags_index`, `/api/tags`) is **not** touched — this is a proposal list.
Cherry-picked winners are folded into `TAG_LIST` by hand (or by a later,
separate step). A short stderr summary prints the per-`n` counts and the total
written.

## Decisions
- **Stop words dropped** from n-gram construction (not just filtered out of the
  list): English + a handful of Hungarian function words (`a`, `egy`, `az`,
  `és`, `a/az`…), justified by the `HU` locale seen in the vision answers.
  Overridable via `--stopwords-file`.
- **`--min-count 2`** default suppresses singletons; `--min-count 1` returns
  everything for full review.
- **Stdlib-only**, no `spacy`; runs under system `/usr/bin/python3` (3.9),
  matching `work5.py`.

## Architecture
`work6.py` is a **dataset-scoped** producer, mirroring `work5.py`: it imports
`config_loader`, has its own `main()`, is invoked standalone
(`python3 work6.py` for the default `.workspace/` env, or `-env ENV` for a
named one — omit `-env` to avoid nesting `.workspace/.workspace`), and writes
one JSONL artifact to the env dir via the same `tempfile` + `os.replace`
atomic-write pattern as `work5.write_result`. `backend.py` auto-runs only
`per_source` works (work1–4); dataset works carry `enabled: false` and are not
in the backend handler table, so registration in `config["works"]` is
declarative only.

Registered as:
```json
{"name": "work6", "scope": "dataset", "handler": "work6",
 "output": "jsonl", "result_file": "work6_tag_candidates.jsonl", "enabled": false}
```

## CLI
```
python3 work6.py [-env ENV] [--input PATH] [--output PATH]
                 [--ngrams 1,2,3] [--min-count 2] [--stopwords-file PATH]
```
- `--input` defaults to `<env_dir>/work1_generic.jsonl`
- `--output` defaults to `<env_dir>/<result_file>` (`work6_tag_candidates.jsonl`)
- `--ngrams` comma list of `n` values (default `1,2,3`)
- `--min-count` drop n-grams below this frequency (default 2; 1 = everything)

## Known limitations
- N-grams are flat and position-free: `foo bar baz` contributes `foo bar`,
  `bar baz` to 2-grams but **not** `foo baz`; no skip-grams.
- A folded token that is also a stopword (e.g. Hungarian `a`) is dropped from
  both 1-grams and as a member of multiword grams.
- Counts are over token occurrences, not over screenshots; a single verbose
  screenshot can dominate. (A per-source variant could count once per screenshot
  per distinct n-gram if needed later.)
- Not integrated into `tags_index.json` / the frontend; curation is manual.

## Review harness (`tag_review.py`)
117k candidate rows are too many to eyeball. `tag_review.py` (stdlib-only) is the
in-between layer that turns the miner's flat list into a curated `TAG_LIST`:

**`build`** — prune to the top-N of each n (default 150 → ~450 rows) and emit a
self-contained `tag_review.html` (DATA inlined) plus a sidecar
`tag_review.html.candidates.jsonl` (id-keyed rows). The page shows every
pruned candidate with filter (free text, n, decision) and per-row keep/drop;
state is kept in `localStorage`, and **Export decisions** downloads
`tag_review.decisions.json` (each row `{id, ngram, count, n, decision}`).
Nothing is pre-dropped by noise category: a high-occurrence token is a
candidate *because* it is common, so keep/drop is the reviewer's call.

**`apply`** — read the decisions JSON back and emit a clean, de-duplicated
`TAG_LIST`. Multiword grams render hyphenated (`sql server` → `sql-server`) to
match the canonical `TAG_LIST` style, and the folded form
(`SQL Server`/`sql server`) de-dups to one tag. `--out` writes one tag per
line (default prints space-joined). The kept tags are the delta to fold into
`config.template.json` `TAG_LIST` by hand.

```
python3 work6.py                                  # -> .workspace/work6_tag_candidates.jsonl
python3 tag_review.py build                        # -> tag_review.html (+ .candidates.jsonl)
#   open tag_review.html, flip keep/drop, Export -> tag_review.decisions.json
python3 tag_review.py apply --decisions tag_review.decisions.json
```
The HTML/sidecar/decisions/`work6_tag_candidates.jsonl` artifacts are
git-ignored.
