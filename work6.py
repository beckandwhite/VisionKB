#!/usr/bin/env python3
"""Work 6: mine frequent n-gram TAG candidates from work1 vision answers.

This is a dataset-wide producer (run separately, like work5.py). It reads the
configured environment's ``work1_generic.jsonl``, keeps only the free-text
``output.answer`` descriptions, and counts 1/2/3-grams over a stopword-filtered
token stream. The result is one flat JSONL for human cherry-picking to extend
the canonical ``TAG_LIST``; it is NOT written into ``tags_index.json``.

Each output line is ``{"ngram", "count", "n"}`` where ``n`` is 1/2/3 and the
list is sorted by descending count. N-grams are formed over the filtered stream
so a "word pair next to each other" means adjacent after stop-removal.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import config_loader

NAME = "work6"

# Diacritic-aware word shape, mirroring ner._NAME_TOKENS so folding is
# consistent with the NER pass.
_WORD = re.compile(r"[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u017F]+"
                   r"(?:['\u2019\u002D\u2013\u2014-][A-Za-z\u00C0-\u017F]+)*")


def _strip_accents(token):
    """Fold case + diacritics: NFD, drop combining marks, lowercase."""
    import unicodedata
    decomposed = unicodedata.normalize("NFD", token)
    base = "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn")
    return base.lower()


# English function words plus a few Hungarian ones (the answers carry HU locale).
DEFAULT_STOPWORDS = {
    # English
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for",
    "from", "had", "has", "have", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "this", "to", "was", "were", "will", "with",
    "you", "your", "i", "we", "he", "she", "them", "they", "me", "my", "we",
    "there", "here", "than", "then", "so", "if", "not", "no", "can", "can",
    "could", "would", "should", "may", "might", "must", "do", "does", "did",
    "done", "up", "out", "off", "over", "under", "again", "once", "very",
    "more", "most", "some", "any", "all", "both", "each", "per", "via",
    "etc", "e.g", "i.e", "also", "only", "still", "already", "now",
    # Hungarian
    "a", "az", "egy", "és", "vagy", "hogy", "mi", "aki", "amely", "nem",
    "azt", "ebben", "ez", "a/az",
}


def load_answers(path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = rec.get("output")
        if isinstance(out, dict) and out.get("answer"):
            yield out["answer"]


def tokenize(text, stopwords):
    tokens = []
    for match in _WORD.findall(text):
        folded = _strip_accents(match)
        if not folded or folded in stopwords:
            continue
        if len(folded) < 2 or folded.isdigit():
            continue
        tokens.append(folded)
    return tokens


def count_ngrams(texts, n_list, stopwords, min_count=1):
    counters = {n: Counter() for n in n_list}
    for text in texts:
        tokens = tokenize(text, stopwords)
        for start in range(len(tokens)):
            for n in n_list:
                end = start + n
                if end > len(tokens):
                    break
                counters[n][" ".join(tokens[start:end])] += 1
    return {n: c for n, c in counters.items() if c}


def load_stopwords(path):
    words = set(DEFAULT_STOPWORDS)
    if path:
        for line in open(path, encoding="utf-8"):
            token = line.strip()
            if token and not token.startswith("#"):
                words.add(token.lower())
    return words


def flatten(counters, min_count):
    rows = []
    for n, counter in counters.items():
        for ngram, count in counter.items():
            if count < min_count:
                continue
            rows.append({"ngram": ngram, "count": count, "n": n})
    rows.sort(key=lambda r: (-r["count"], r["n"], r["ngram"].lower()))
    return rows


def write_result(path, rows, counters, min_count):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    now = datetime.now(tz=timezone.utc).isoformat()
    meta = {
        "run_id": run_id,
        "generated_at": now,
        "min_count": min_count,
        "per_n": {n: len(c) for n, c in sorted(counters.items())},
    }
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return run_id, meta


def main():
    parser = argparse.ArgumentParser(
        description="Work 6: mine frequent n-gram TAG candidates from work1.")
    parser.add_argument("-env", default=config_loader.DEFAULT_ENV,
                        help="environment name; omit for the default (.workspace/). "
                        "An unknown name is auto-created on first run.")
    parser.add_argument("--input", default=None,
                        help="override the work1_generic.jsonl path")
    parser.add_argument("--output", default=None,
                        help="override the work6 candidate JSONL path")
    parser.add_argument("--ngrams", default="1,2,3",
                        help="comma list of n values to mine (default 1,2,3)")
    parser.add_argument("--min-count", type=int, default=2,
                        help="drop n-grams below this frequency (default 2)")
    parser.add_argument("--stopwords-file", default=None,
                        help="optional extra stopword file (one per line, #"
                        " comments ok); augments the built-in list")
    args = parser.parse_args()

    try:
        n_list = [int(x) for x in args.ngrams.split(",") if x.strip()]
    except ValueError:
        raise SystemExit("--ngrams must be a comma list of integers, got %r"
                          % args.ngrams)
    if not n_list:
        raise SystemExit("--ngrams must list at least one value")

    _env, config = config_loader.resolve_environment(args.env)
    input_path = args.input or str(config["env_dir"] / "work1_generic.jsonl")
    output_path = (args.output
                   or str(config["env_dir"]
                          / result_file_for(config, "work6_tag_candidates.jsonl")))

    if not os.path.isfile(input_path):
        raise SystemExit("input not found: %s" % input_path)

    stopwords = load_stopwords(args.stopwords_file)
    texts = list(load_answers(input_path))
    print("loaded %d answers; stopwords=%d" % (len(texts), len(stopwords)),
          file=sys.stderr)

    counters = count_ngrams(texts, n_list, stopwords)
    rows = flatten(counters, args.min_count)
    run_id, meta = write_result(output_path, rows, counters, args.min_count)

    per_n = ", ".join("%d-gram=%d" % (n, meta["per_n"][n])
                      for n in sorted(meta["per_n"]))
    print("Tag miner %s: %s, min_count=%d, %d candidate(s) -> %s"
          % (run_id, per_n, args.min_count, len(rows), output_path))


def result_file_for(config, default):
    """Resolve the configured work6 result_file (falls back to a default)."""
    for work in config.get("works", []):
        if work.get("name") == NAME:
            return work.get("result_file") or default
    return default


if __name__ == "__main__":
    main()
