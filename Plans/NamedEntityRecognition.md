# Named Entity Recognition

**Date:** 2026-08-25
**Repository:** VisionKB

## Goal
Populate TAGs from `work1_generic.jsonl` by extracting People, Org, and
Location entities from the `output.answer` field (free-text screenshot
descriptions), so downstream tagging can reference proper nouns.

## Pipeline
1. **Load** the JSONL and keep only `output.answer`; skip records whose
   `output` is null (error records).
2. **Regex pre-pass.** People are pulled from name *lists* only, gated by list
   context (`participant`, `attendee`, `presenter`, `contact`, `account`,
   `left to right`, …). The signal is **structure** (delimiters, repetition),
   not capitalization, so uncapitalized names are not missed.
    - Role/code suffixes are stripped: `Zrak, Janos – SAP Team Lead`,
      `Abiy Gizaw - C-53219`.
    - Separators: `;` first, then `,`. A 2-token `Family, Given` (Hungarian
      order) is reordered to `Given Family`; a longer comma run is a Western
      comma-list; a single token is kept as-is.
3. **spaCy NER.** English model (`en_core_web_lg`) primary, since the text is
   mostly English; `hu`/`de` are optional (`--language`). Extracts
   `ORG`, `GPE/LOC/FAC`, and merges `PER/PERSON` with the regex persons.
   Model load is guarded: if a model is missing, the run continues with the
   regex layer only and warns.
4. **Fold-dedup.** Keys fold case **and** diacritics, so `Zrak, Janos`,
    `Zrak, János`, `János Zrak`, `ADAM WITTEK` collapse to one entry; the
    most-frequent spelling is the canonical display, and all raw spellings are
    kept in `variants`. Two-token names also key on the **sorted** token pair,
    so `Tamás Beck` / `Beck Tamás` merge.

## Output
Grouped to stdout as `People:` / `Org:` / `Location:`, and one JSON object per
entity written to `named_entities.jsonl`:
`{"type", "display", "variants", "count"}`.

## Known limitations
- Pure `Family Given` without a comma (e.g. `Becker Zoltán`) is order-ambiguous;
  we do not force an order — we dedup on the token set instead.
- Regex persons depend on list context; names mentioned in prose (not in a list)
  are covered only by the spaCy `PER`/`PERSON` layer.
- `ORG`/`Location` come straight from a general English model over prose, so they
  carry more noise than the regex-driven people list; the top of each list is
  clean and degrades with count.

## Environment
System `/usr/bin/python3` is 3.9, which cannot build current `spacy`/`thinc`.
Run inside the repo `.venv` (created with `uv venv --python 3.11 .venv`,
`uv pip install --python .venv/bin/python spacy`).

## Usage
`.venv/bin/python ner.py .workspace/work1_generic.jsonl`
(Add `--language hu_core_news_lg` / `de_core_web_lg` for Hungarian/German passes.)
