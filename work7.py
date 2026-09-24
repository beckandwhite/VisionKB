#!/usr/bin/env python3
"""Work 7: a growing, typed canonical tag registry with an AKA layer.

This is a dataset-wide producer (run separately, like work5/work6). It is the
consolidation + tagging layer described in Plans/NERv3.md.

Why a new artifact: ``named_entities.jsonl`` already collapses the *trivial*
aliases (case / diacritics / Hungarian-vs-Western word order, e.g. "Wittek
Adam" ~= "Adam Wittek") inside its ``variants`` field, but it cannot hold a
*semantic* AKA like "Bravo" ~= "Adam Wittek" (his team), and its ``type`` field
is unreliable. This work builds a per-environment canonical registry
(``canonical_tags.json``) that:

    * ortho-merges surface spellings into one canonical entity (case-folded),
    * re-derives a 7-type classification (the source type is not trusted),
    * records each AKA with a ``kind`` (orthographic = auto, aka = curated) and a
      ``relation`` (identity / member / team / aka / part-of), and
    * carries the occurrence ``count`` so the list stays dense yet faithful.

Subcommands (all stdlib-only, Python 3.9, atomic tempfile+os.replace writes):

  build   ingest named_entities.jsonl (+ optional work6 candidates) -> build an
          ortho-merged, typed, AKA-proposed canonical_tags.json.
  resolve fold a registry into an alias index and tag each screenshot from
          its work1 answer, emitting canonical tags[] + an unresolved.log
            (unmatched mentions = next run's alias proposals = the growth feed).
  apply   fold a tag_review entities decisions JSON back into the registry
            (approve/confirm AKA proposals; append-only).

The trust rule (decided): ``orthographic`` aliases apply automatically;
``aka`` links are *proposed* by heuristics (co-occurrence, fuzzy, structural)
and *human-confirmed* via ``tag_review.py entities`` + ``work7 apply``.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import config_loader

REPO_ROOT = Path(__file__).resolve().parent

NAME = "work7"

# 7-type taxonomy (Option A): Person | Place | Group | Product | Scene |
# Concept | Other.   "Other" is the universal catch-all ("unclassified").
TYPES = ("Person", "Place", "Group", "Product", "Scene", "Concept", "Other")

# Diacritic-aware word shape, copied from ner._NAME_TOKENS so folding stays
# consistent with the NER pass.
_WORD = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['\u2019\-][A-Za-zÀ-ÖØ-öø-Ÿ]+)*"
)

# --- heuristics: small knowledge lists. Additive + overridable, never deleted ---

_PLACE_WORDS = {
    "hungary", "budapest", "frankfurt", "bratislava", "israel", "europe",
    "potsdam", "north sea", "united kingdom", "united states", "columbus",
    "benzinkut", "weu",
}
_GROUP_SUFFIX = ("squad", "team", "daily", "crew", "pod", "chapter")
_GROUP_PREFIX = ("team ",)
_PRODUCT_WORDS = {
    "sap", "microsoft", "azure", "aws", "git", "github", "gitlab", "google",
    "apple", "datadog", "kyma", "grafana", "jira", "confluence", "terraform",
    "argocd", "jenkins", "kafka", "kubernetes", "kube", "kibana", "mssql",
    "sqlserver", "sql server", "ssms", "powershell", "onenote", "slack",
    "teams", "microsoft teams", "facetime", "youtube", "facebook", "share",
    "sharepoint", "chrome", "safari", "firefox", "edge", "vscode",
    "visual studio code", "btp", "gigya", "hermes", "claude", "opencode",
    "mactop", "mcp", "llm", "cdc", "cpro", "onedrive", "icloud", "moldeco",
}
_UI_WORDS = {
    "launchpad", "ide", "server", "chrome", "tab", "window", "panel", "dock",
    "menu", "bar", "app", "studio", "console", "terminal", "client",
}
_CONCEPT_WORDS = {
    "devops", "cdc", "cpro", "btp", "gitops", "ci", "cd", "kpi", "ocr", "dns",
    "ram", "gpu", "cpu", "soc", "iops", "utc", "mcp", "llm", "kyma", "ion",
}
_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
         "with", "from", "by", "as", "is", "are"}


# ----------------------------------------------------------------------------
# Folding / canonical keys (copied from ner.py). ner.py imports spacy at module
# top, so work7 (stdlib/3.9) cannot import it; the ortho-merge logic is copied.
# ----------------------------------------------------------------------------

def fold(text):
    """NFD-fold case + diacritics: macos/MacOS / Tamas/Tamas collapse."""
    decomposed = unicodedata.normalize("NFD", text)
    base = "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn")
    return base.lower()


def _has_diacritic(s):
    return any("À" <= ch <= "ſ" for ch in s)


def norm_key(tokens):
    """Canonical token-set key. 2 tokens are order-ambiguous (HU Family,Given vs
    Western Given,Family), so key on the sorted pair; 3+ keep order. Deduped."""
    seen = set()
    kept = []
    for t in tokens:
        f = fold(t)
        if not f or f in seen:
            continue
        seen.add(f)
        kept.append(f)
    if len(kept) == 2:
        kept = sorted(kept)
    return tuple(kept)


def pick_display(variants):
    """Most-frequent, diacritic-preferring, non-ALLCAPS spelling wins."""
    counts = Counter(variants)

    def score(v):
        return (1 if _has_diacritic(v) else 0,
                0 if v.isupper() else 1,
                counts[v])

    return max(variants, key=score)


def _tokens(text):
    return _WORD.findall(text)


def slug(canonical):
    """Stable, env-independent id: fold diacritics, spaces -> dashes,
    keep [a-z0-9-][:80].  Folds first so "Ádám Wittek" -> "adam-wittek"
    (a diacritic-dropping slug would corrupt it to "dm-wittek")."""
    folded = re.sub(r"[\s]+", "-", fold(canonical).strip())
    folded = re.sub(r"[^a-z0-9\-]", "", folded)
    return folded[:80]


# ----------------------------------------------------------------------------
# 7-type engine
# ----------------------------------------------------------------------------

def recommend_type(display, count):
    """Heuristic type recommendation -> (type, confidence 0..1).

    Concrete evidence (a wordlist hit: Place/Product/Group/Concept, a Team shape,
    a hyphenated Scene) is trusted and wins.  A bare capitalized word with no
    concrete evidence falls back to Person, and nothing else to Other.  Confidence
    is top/sum so a human can see how unsure the engine is and override in the
    review harness."""
    toks = _tokens(display or "")
    lowered = [fold(t) for t in toks]
    n = len(lowered)
    joined = fold(display)
    scores = defaultdict(float)
    concrete = False

     # Group: Team X / X Squad / X daily / bare "team" shape.
    for suffix in _GROUP_SUFFIX:
        if joined.endswith(suffix) or joined.endswith(suffix + "s"):
            scores["Group"] += 1.2
            concrete = True
            break
    for prefix in _GROUP_PREFIX:
        if joined.startswith(prefix):
            scores["Group"] += 1.2
            concrete = True
            break

     # Place: wordlist hit (concrete, dominates the generic person fallback).
    if joined in _PLACE_WORDS or any(l in _PLACE_WORDS for l in lowered):
        scores["Place"] += 1.2
        concrete = True

     # Product: software wordlist hit OR adjacency to a UI word.
    if any(l in _PRODUCT_WORDS for l in lowered):
        scores["Product"] += 1.2
        concrete = True
    for l in lowered:
        if l in _UI_WORDS:
            scores["Product"] += 0.4
            break

     # Concept: abbreviation/topic wordlist hit.
    if any(l in _CONCEPT_WORDS for l in lowered):
        scores["Concept"] += 1.0
        concrete = True

     # Scene: a hyphenated/compound phrase (from work6 candidates).
    if "-" in display and n >= 2:
        scores["Scene"] += 1.2
        concrete = True

     # Person: only as a fallback — a capitalised 1-2 token name with no
     # concrete Place/Product/Group/Concept/Scene evidence.
    if not concrete and display and any(t[:1].isupper() for t in toks) \
            and n <= 2 and not any(l in _PRODUCT_WORDS for l in lowered):
        scores["Person"] += 1.0 if n <= 1 else 0.8

    total = sum(scores.values())
    if not total:
        return "Other", 0.0
    best_type = max(scores, key=lambda k: (scores[k], TYPES.index(k)))
    return best_type, round(scores[best_type] / total, 2)


# ----------------------------------------------------------------------------
# Ingestion: ortho-merge
# ----------------------------------------------------------------------------

def load_entities(path):
    """Yield {display, variants, count} records; the source `type` is ignored
    (it is unreliable) and re-derived by recommend_type."""
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        display = rec.get("display")
        if not display:
            continue
        variants = rec.get("variants") or [display]
        yield {"display": display,
               "variants": [v for v in variants if v],
               "count": int(rec.get("count", 0) or 0)}


def ortho_merge(entities):
    """Collapse entities that fold to the same token-set key into one canonical.

    This is the *trivial* AKA layer (case / diacritics / word-order).  Returns a
    list of canonical dicts; no status filtering yet."""
    buckets = defaultdict(lambda: {"variants": set(), "count": 0, "key": None})
    for e in entities:
        tokens = _tokens(e["display"])
        key = norm_key(tokens)
        if len(key) < 1:
            continue
        b = buckets[key]
        b["key"] = key
        b["count"] += e["count"]
        b["variants"].update(e["variants"])
        b["variants"].add(e["display"])

    canon = []
    for key, b in buckets.items():
        variants = sorted(b["variants"])
        canonical = pick_display(variants)
        typ, conf = recommend_type(canonical, b["count"])
        canon.append({
            "id": slug(canonical),
            "type": typ,
            "confidence": conf,
            "canonical": canonical,
            "aliases": [
                {"form": v, "kind": "orthographic", "relation": "identity"}
                for v in variants
                if v != canonical and fold(v) != fold(canonical)
            ],
            "count": b["count"],
            "status": "active",
            "proposed_aka": [],
        })
    canon.sort(key=lambda x: (-x["count"], x["type"].lower(),
                              x["canonical"].lower()))
    return canon


# ----------------------------------------------------------------------------
# AKA proposers (auto-propose; human confirms).  All read the free-text answers.
# ----------------------------------------------------------------------------

def load_answers(path):
    """Yield answer strings from a work1_generic.jsonl (or a .answers.jsonl)."""
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
        elif isinstance(out, str) and out:
            yield out


# "X - Y" (dash) and "X -> Y" (arrow): the clearest "X is also Y / maps to Y"
# signal in the answers.  X is the "from"/surface, Y the "to"/complete form.
_QUOTE = r"[\u201c\u2018'\"]"
_NAME = r"[A-ZÀ-ÖØ-öø-ÿ]"
_REST = r"[A-Za-zÀ-ÖØ-öø-ÿ\u2019' -]{1,30}"
_STRUCT_QUOTED_DASH = re.compile(
    r"(" + _QUOTE + r")(" + _NAME + _REST + r")(" + _QUOTE + r")"
    r"\s*[\u2013\u2014\-]\s*"
    r"(" + _NAME + _REST + r")"
)
_STRUCT_ARROW = re.compile(
    r"(" + _NAME + _REST + r")\s*\u2192\s*(" + _NAME + _REST + r")"
)


def _clean(form):
    form = form.strip().strip("'\"*`-–—")
    toks = [t for t in form.split()
            if fold(t) not in _STOP and (len(t) > 1 or _has_diacritic(t))]
    return " ".join(toks) if toks else form.strip()


def propose_structural(texts, canon):
    """Propose aka links from quoted-dash and arrow mappings in the answers.

    "Bravo - Adam" -> aka(Bravo -> Adam Wittek).  Each proposal points ``alias``
    (the surface) at ``target_alias`` (the complete form); attach_proposals keys
    it onto the right canonical."""
    proposals = []
    for text in texts:
        for m in _STRUCT_QUOTED_DASH.finditer(text):
            x, y = _clean(m.group(2)), _clean(m.group(4))
            if x and y and fold(x) != fold(y):
                proposals.append(_mk_aka(x, y, "member",
                                         "structural:%s" % text[:40]))
        for m in _STRUCT_ARROW.finditer(text):
            x, y = _clean(m.group(1)), _clean(m.group(2))
            if x and y and fold(x) != fold(y) and " " in y:
                # require a multi-token target: "Bravo -> Barkuni" (a Jira
                 # project-code assignment) is dropped, while "X -> Adam Wittek"
                 # (a real name) survives.
                proposals.append(_mk_aka(x, y, "aka", "structural-arrow"))
    return _dedup_proposals(proposals)


def _mk_aka(x, y, relation, evidence):
    return {"alias": x, "target_alias": y, "relation": relation,
            "evidence": evidence}


def _dedup_proposals(proposals):
    by_key = {}
    for p in proposals:
        key = (fold(p["alias"]), fold(p["target_alias"]))
        if key[0] == key[1]:
            continue
        by_key.setdefault(key, {"alias": p["alias"],
                                "target_alias": p["target_alias"],
                                "relation": p["relation"],
                                "evidence": p["evidence"], "count": 0})
        by_key[key]["count"] += 1
    out = []
    for p in by_key.values():
        out.append({"alias": p["alias"], "target_alias": p["target_alias"],
                    "relation": p["relation"], "count": p["count"],
                    "evidence": p["evidence"]})
    return out


def propose_fuzzy(canon, threshold=0.6, max_forms=5000):
    """Propose aka between two canons whose folded forms are near-duplicates
    (token Jaccard + suffix-variant form).  E.g. "Adam Witteks" -> "Adam
    Wittek", "Wittek" ~= "Witek".

    Token-indexed so it is near-linear rather than O(n^2): index canons by their
    folded (de-suffixed) tokens and only compare canons that share a rare
    token."""
    forms = [(fold(e["canonical"]), e) for e in canon]
    forms = sorted(forms, key=lambda f: -len(f[0]))[:max_forms]
    tok_to_ids = defaultdict(list)
    for i, (fi, e) in enumerate(forms):
        for tok in set(_tokens(fi)):
            tok_to_ids[_primary_stem(tok)].append(i)
    seen_pairs = set()
    proposals = []
    for tok, idxs in tok_to_ids.items():
        if len(tok) < 3:
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if i > j:
                    i, j = j, i
                pair = (i, j)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                fi, ei = forms[i]
                fj, ej = forms[j]
                if fi == fj or abs(len(fi) - len(fj)) > 4:
                    continue
                sim = _variant_sim(fi, fj)
                if sim >= threshold:
                    target = ej if ej["count"] >= ei["count"] else ei
                    source = ei if target is ej else ej
                    proposals.append({"alias": source["canonical"],
                                      "target": target["id"],
                                      "relation": "aka",
                                      "count": source["count"],
                                      "evidence": "fuzzy=%.2f" % sim})
    return proposals


_SUFFIXES = ("ss", "es", "s", "ed", "ing", "er", "ers", "ness", "ish")


def _desuffix_stem(token):
    """Return a short list of de-suffixed stems so 'witteks' ~ 'wittek'."""
    stems = {token}
    for suf in _SUFFIXES:
        if len(token) - len(suf) >= 3 and token.endswith(suf):
            stems.add(token[: -len(suf)])
    return stems if len(stems) > 1 else {token}


def _primary_stem(token):
    """Single hashable index key: the longest de-suffixed stem (or token)."""
    stems = _desuffix_stem(token)
    return max(stems, key=len)


def _variant_sim(a, b):
    """Combined token-Jaccard and singular-form similarity (0..1)."""
    fa = set(t for t in _tokens(a))
    fb = set(t for t in _tokens(b))
    if not fa or not fb:
        return 0.0
    inter = sum(1 for x in fa if any(_eq(x, y) for y in fb))
    sim = inter / len(fa | fb)
    return round(sim, 3)


def _eq(x, y):
    """Token-level equivalence: exact or shared de-suffix stem."""
    if x == y:
        return True
    sx, sy = _desuffix_stem(x), _desuffix_stem(y)
    return bool(sx & sy) and min(len(x), len(y)) >= 3


def propose_cooccur(texts, canon, min_cooc=3, max_distinct=400,
                    max_answers=4000):
    """Co-occurrence: two canons that recur together get an aka proposal.

    Needed-not-sufficient (unrelated surfaces can recur together), so this only
    *proposes*; a human confirms.  Bounded to the most-frequent canons to stay
    fast on large corpora."""
    top = canon[:max_distinct]
    surfaces = []
    for e in top:
        forms = [fold(e["canonical"])] + [fold(a["form"])
                                          for a in e.get("aliases", [])]
        surfaces.append((e["id"], [f for f in forms if len(f) >= 3]))

    cooc = Counter()
    for k, text in enumerate(texts):
        if k >= max_answers:
            break
        low = text.lower()
        hits = [eid for eid, forms in surfaces if any(f in low for f in forms)]
        for a in range(len(hits)):
            for b in range(a + 1, len(hits)):
                cooc[(hits[a], hits[b])] += 1

    canon_by_id = {e["id"]: e for e in canon}
    proposals = []
    for (a, b), c in cooc.most_common():
        if c < min_cooc:
            continue
        ea, eb = canon_by_id[a], canon_by_id[b]
        # type-aware: only link entities of the same type, so a person never
        # becomes an AKA of "IDE"/"Chat"/"Time" merely because they co-occur.
        if ea["type"] != eb["type"]:
            continue
        target = eb if eb["count"] >= ea["count"] else ea
        source = ea if target is eb else eb
        proposals.append({"alias": source["canonical"],
                          "target": target["id"], "relation": "aka",
                          "count": c, "evidence": "cooccur=%d answers" % c})
    return proposals


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------

def add_scene_entities(candidates_path, canon, min_count, per_n=200):
    """Promote frequent multiword n-grams from work6 into Scene canonicals.

    Capped to the top ``per_n`` per n-gram width so a 100k-candidate file does
    not explode the registry (and the fuzzy proposer that follows)."""
    if not candidates_path or not os.path.isfile(candidates_path):
        return canon
    by_n = defaultdict(list)
    for line in open(candidates_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("n", 1) < 2 or rec.get("count", 0) < min_count:
            continue
        by_n[rec.get("n", 1)].append((rec["count"], rec["ngram"]))
    scene_tokens = set()
    for n, items in by_n.items():
        items.sort(key=lambda it: (-it[0], it[1].lower()))
        for _, ngram in items[:per_n]:
            scene_tokens.add(ngram)
    if not scene_tokens:
        return canon
    canon = list(canon)
    have = {fold(e["canonical"]) for e in canon}
    for ngram in sorted(scene_tokens):
        key = fold(ngram)
        if key in have:
            continue
        typ, conf = recommend_type(ngram, 0)
        canon.append({
            "id": slug(ngram),
            "type": typ,
            "confidence": conf,
            "canonical": re.sub(r"\s+", "-", ngram.strip()).lower(),
            "aliases": [{"form": ngram, "kind": "orthographic",
                         "relation": "identity"}],
            "count": 0,
            "status": "active",
            "proposed_aka": [],
        })
        have.add(key)
    return canon


def attach_proposals(canon, proposals):
    """Add heuristic AKA proposals to each entity's ``proposed_aka`` review
    queue (NOT ``aliases``).

    This is the crux of "propose, don't auto-confirm": fuzzy/structural/cooccur
    are heuristics that produce noise (the source type is unreliable, and
    co-occurrence is necessary-not-sufficient), so their output is a *prioritized
    queue for a human*, not active tags.  Only ``orthographic`` (auto, safe) and
    human-``confirmed`` aliases live in ``aliases`` and feed the resolution
    index.  A confirmed proposal is promoted by ``apply``.

    Proposer shapes differ: structural carries ``target_alias`` (the "to"
    string); fuzzy/cooccur carry ``target`` (a canonical id).  Resolve either and
    fall back to best token-overlap so all three attach cleanly."""
    by_fold = {}
    by_id = {}
    for e in canon:
        by_fold.setdefault(fold(e["canonical"]), e)
        by_id[e["id"]] = e
        for a in e["aliases"]:
            by_fold.setdefault(fold(a["form"]), e)
    attached = 0
    for p in proposals:
        target = None
        if p.get("target_alias"):
            target = by_fold.get(fold(p["target_alias"]))
        if p.get("target") and target is None:
            target = by_id.get(p["target"])
        if target is None:
            target = _best_target(canon, p)
        if not target:
            continue
        alias_form = p["alias"]
        if any(fold(a["form"]) == fold(alias_form) for a in target["aliases"]):
            continue
        if any(fold(x["form"]) == fold(alias_form)
               for x in target.get("proposed_aka", [])):
            continue
        new_prop = {
            "form": alias_form,
            "kind": "aka",
            "relation": p.get("relation", "aka"),
            "count": p.get("count", 0),
            "evidence": p.get("evidence", ""),
         }
        target.setdefault("proposed_aka", []).append(new_prop)
        attached += 1
    return canon, attached


def _best_target(canon, p):
    """Last-resolve a structural proposal whose ``target_alias`` string is not
    yet a canonical.  Only attach when the target form is itself multi-token and
    shares a token with a canonical, so a single noisy alias does not scatter."""
    q = p.get("target_alias", "")
    if not q:
        return None
    qtoks = _tokens(q)
    if len(qtoks) < 2:
        return None
    qf = [fold(t) for t in qtoks]
    if not qf or all(f in _STOP for f in qf):
        return None
    best, best_score = None, 0
    for e in canon:
        etoks = [fold(t) for t in _tokens(e["canonical"])]
        for qt in qf:
            for et in etoks:
                if qt == et or _eq(qt, et):
                    best, best_score = e, 1
                    break
            if best:
                break
    return best if best else None


def finalize(canon, backup, repo_root):
    """Drop empty canons, sort.  Optionally back up aka knowledge to git root."""
    canon = [e for e in canon if e["count"] > 0 or e.get("aliases")]
    canon.sort(key=lambda x: (-x["count"], x["type"].lower(),
                              x["canonical"].lower()))
    if backup:
        aka = [
            {"canonical": e["canonical"], "type": e["type"],
             "aliases": [a for a in e.get("aliases", [])
                         if a.get("kind") == "aka"]}
            for e in canon
            if any(a.get("kind") == "aka" for a in e.get("aliases", []))
        ]
        backup_path = Path(repo_root) / "aliases.curated.json"
        atomic_write_json(backup_path, aka)
        print("  backed up %d aka-bearing entities to %s"
              % (len(aka), backup_path), file=sys.stderr)
    return canon


# ----------------------------------------------------------------------------
# resolve
# ----------------------------------------------------------------------------

def load_registry(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_alias_index(registry):
    """Build a two-tier alias index over active entities + confirmed/human aka.

    Returns (single, multi):
     * ``single``: folded single-word form -> canonical id (matches per token).
     * ``multi``:  (folded-phrase, canonical id) for 2+ word forms, checked as
       whole-phrase substrings.
    Proposed-only (proposed_aka) forms are excluded: a proposal is not yet a tag.
    Forms shorter than 3 folded chars are dropped so scene fragments ("l", "n s"...)
    cannot pollute tagging."""
    single = {}
    multi = []
    for e in registry:
        if e.get("status") == "deprecated":
            continue
        forms = [e["canonical"]]
        for a in e.get("aliases", []):
            if a.get("status", "active") != "deprecated" \
                    and (a.get("kind") != "aka" or a.get("by") == "human"):
                forms.append(a["form"])
        for form in forms:
            f = fold(form)
            if len(f) < 3:
                continue
            if " " in f:
                multi.append((f, e["id"], len(f)))
            else:
                single.setdefault(f, e["id"])
    multi.sort(key=lambda x: -x[2])
    return single, multi


def resolve_image(text, single, multi):
    """Return (canonical_ids, unresolved_surfaces) for one answer.

    Multi-word aliases match as whole-phrase substrings; single-word aliases match
    per token (folded), so "All" does not match a sentence and "bravo" matches only
    a real mention.  Unresolved are proper-noun-ish tokens that matched nothing yet
    (the growth feed for the next build)."""
    found = set()
    unresolved = set()
    low = text.lower()
    seen = set()
    for phrase, cid, _ in multi:
        if phrase in seen or len(phrase) < 3:
            continue
        if phrase in low:
            found.add(cid)
            seen.add(phrase)
    for tok in _WORD.findall(text):
        f = fold(tok)
        if len(f) < 3:
            continue
        cid = single.get(f)
        if cid:
            found.add(cid)
            continue
        if tok[0].isupper() or _has_diacritic(tok):
            unresolved.add(tok)
    return sorted(found), sorted(unresolved)


def cmd_resolve(args):
    registry = load_registry(args.registry)
    single, multi = build_alias_index(registry)
    by_id = {e["id"]: e["canonical"] for e in registry}

    out_records = []
    unresolved_counter = Counter()
    for line in open(args.input, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out = rec.get("output")
        text = (out.get("answer", "") if isinstance(out, dict)
                else out if isinstance(out, str) else "")
        cids, unresolved = resolve_image(text, single, multi)
        out_records.append({
             "source_key": rec.get("source_key", ""),
             "filename": rec.get("filename", ""),
             "tags": [by_id[c] for c in cids if c in by_id],
             "unresolved": unresolved,
         })
        unresolved_counter.update(unresolved)

    if args.out:
        atomic_write_jsonl(args.out, out_records)
    if args.unresolved_log:
        with open(args.unresolved_log, "w", encoding="utf-8") as fh:
            for form, c in unresolved_counter.most_common():
                fh.write(json.dumps({"form": form, "count": c},
                                    ensure_ascii=False) + "\n")
    total_tags = sum(len(r["tags"]) for r in out_records)
    top = ", ".join("%s=%d" % (by_id.get(k, k), v)
                    for k, v in Counter(
                        t for r in out_records for t in r["tags"])
                    .most_common(10))
    print("resolve: %d image(s), index=%d single + %d multi forms, "
            "%d tag-hits; top tags: %s"
            % (len(out_records), len(single), len(multi), total_tags, top))
    return 0


# ----------------------------------------------------------------------------
# apply
# ----------------------------------------------------------------------------

def cmd_apply(args):
    registry = load_registry(args.registry)
    with open(args.decisions, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        payload = payload.get("decisions") or []
    by_id = {e["id"]: e for e in registry}

    approved = denied = 0
    for d in payload:
        cid = d.get("entity_id") or d.get("id")
        alias = d.get("alias") or d.get("form")
        decision = str(d.get("decision", "pending")).lower()
        if decision in ("pending", "") or not cid or not alias:
            continue
        ent = by_id.get(cid)
        if not ent:
            continue
        if decision in ("confirm", "keep"):
            if promote_alias(ent, alias):
                approved += 1
                ent["status"] = "active"
        elif decision == "drop":
            if demote_alias(ent, alias):
                denied += 1
    atomic_write_json(args.registry, registry)
    print("apply: %d approved, %d denied in %s"
          % (approved, denied, args.registry))
    return 0


def promote_alias(ent, alias_form):
    """Confirm an aka: promote a queued proposal to a confirmed human AKA, or
    create a new one (the human supplying knowledge no proposer found).""",
    key = fold(alias_form)
    for x in ent.get("proposed_aka", []):
        if fold(x["form"]) == key:
            new_alias = {
                "form": x["form"],
                "kind": "aka",
                "relation": x.get("relation", "aka"),
                "count": x.get("count", 0),
                "by": "human",
                "conf_at": x.get("evidence", ""),
            }
            ent.setdefault("aliases", []).append(new_alias)
            ent["proposed_aka"] = [y for y in ent["proposed_aka"]
                                   if fold(y["form"]) != key]
            return True
    # no proposal: accept a human-supplied new AKA (knowledge over time)
    for a in ent.get("aliases", []):
        if fold(a["form"]) == key:
            a["by"] = "human"
            a["kind"] = "aka"
            a.pop("evidence", None)
            return True
    ent.setdefault("aliases", []).append({
        "form": alias_form,
        "kind": "aka",
        "relation": "aka",
        "count": 0,
        "by": "human",
        "conf_at": "human",
    })
    if ent.get("status") != "active":
        ent["status"] = "active"
    return True


def demote_alias(ent, alias_form):
    """Drop a proposed aka (mark it deprecated so it won't recur next build)."""
    key = fold(alias_form)
    for x in ent.get("proposed_aka", []):
        if fold(x["form"]) == key:
            x["status"] = "deprecated"
            return True
    for a in ent.get("aliases", []):
        if a.get("kind") == "aka" and fold(a["form"]) == key:
            a["status"] = "deprecated"
            return True
    return False


# ----------------------------------------------------------------------------
# io helpers
# ----------------------------------------------------------------------------

def atomic_write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            for rec in records:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(obj, out, ensure_ascii=False, indent=2)
            out.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------

def cmd_build(args):
    env, config = config_loader.resolve_environment(args.env)
    env_dir = config["env_dir"]
    entities_path = args.input or str(env_dir / "named_entities.jsonl")
    candidates_path = (args.candidates
                       if args.candidates is not None
                       else str(env_dir / "work6_tag_candidates.jsonl"))
    registry_path = args.out or str(env_dir / "canonical_tags.json")
    answers_path = args.answers or str(env_dir / "work1_generic.jsonl")

    if not os.path.isfile(entities_path):
        raise SystemExit("named entities input not found: %s" % entities_path)

    print("build: loading %s" % entities_path, flush=True)
    entities = list(load_entities(entities_path))
    print("  %d raw entities" % len(entities), file=sys.stderr)

    answers = []
    want_answers = args.cooc or args.structural
    if os.path.isfile(answers_path) and want_answers:
        answers = list(load_answers(answers_path))
        print("  %d answers for proposers" % len(answers), file=sys.stderr)

    canon = ortho_merge(entities)
    n_entities = len(canon)
    canon = add_scene_entities(candidates_path, canon, args.min_count,
                               args.per_n)
    canonical_before = len(canon)
    n_scene = canonical_before - n_entities

    proposals = []
    if args.fuzzy:
        proposals += propose_fuzzy(canon)
    if answers and args.structural:
        proposals += propose_structural(answers, canon)
    if answers and args.cooc:
        proposals += propose_cooccur(answers, canon, min_cooc=args.min_count)
    attached = 0
    if proposals:
        canon, attached = attach_proposals(canon, proposals)

    canon = finalize(canon, args.backup, REPO_ROOT)
    atomic_write_json(registry_path, canon)

    n_ortho = sum(len([a for a in e["aliases"]
                       if a["kind"] == "orthographic"]) for e in canon)
    n_aka = sum(len([a for a in e["aliases"] if a["kind"] == "aka"])
                for e in canon)
    n_proposed = sum(len(e.get("proposed_aka", [])) for e in canon)
    by_type = Counter(e["type"] for e in canon)
    run_id = uuid.uuid4().hex[:8]
    print("build %s: %d canons (%d from entities + %d scenes), "
           "%d ortho + %d aka aliases, %d proposals attached -> %s"
          % (run_id, len(canon), n_entities, n_scene, n_ortho,
             n_aka, n_proposed, registry_path))
    print("  by type: " + ", ".join("%s=%d" % (t, by_type[t])
                                    for t in TYPES if by_type[t]),
          file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Work 7: growing, typed canonical tag registry with AKA.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build canonical_tags.json (+ AKA proposals)")
    b.add_argument("-env", default=config_loader.DEFAULT_ENV,
                   help="environment (omit for the default .workspace/).")
    b.add_argument("--input", default=None,
                   help="named_entities.jsonl "
                        "(default env_dir/named_entities.jsonl).")
    b.add_argument("--candidates", default=None,
                   help="work6_tag_candidates.jsonl for Scene entities "
                        "(pass '' to skip).")
    b.add_argument("--answers", default=None,
                   help="work1_generic.jsonl answer corpus for proposers.")
    b.add_argument("--out", default=None,
                   help="output registry (default env_dir/canonical_tags.json).")
    b.add_argument("--min-count", type=int, default=2,
                   help="min count for scene entities / cooccur threshold.")
    b.add_argument("--per-n", type=int, default=200,
                   help="top-N scene n-grams kept per n width (default 200).")
    b.add_argument("--fuzzy", action="store_true", default=True,
                   help="run the fuzzy AKA proposer (default on).")
    b.add_argument("--no-fuzzy", dest="fuzzy", action="store_false")
    b.add_argument("--structural", action="store_true", default=True,
                   help="run the structural (quoted-dash / arrow) proposer.")
    b.add_argument("--no-structural", dest="structural", action="store_false")
    b.add_argument("--cooc", action="store_true",
                   help="run the co-occurrence proposer (slower; opt-in).")
    b.add_argument("--no-cooc", dest="cooc", action="store_false")
    b.add_argument("--backup", action="store_true",
                   help="copy aka knowledge to git-tracked aliases.curated.json.")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("resolve", help="tag each screenshot against the registry")
    r.add_argument("--registry", default="canonical_tags.json",
                   help="canonical registry JSON (default canonical_tags.json).")
    r.add_argument("--input", required=True,
                   help="work1_generic.jsonl (or similar) to tag.")
    r.add_argument("--out", default=None,
                   help="write per-image {source_key, tags, unresolved}.")
    r.add_argument("--unresolved-log", default=None,
                   help="write the unmatched-mentions growth feed.")
    r.set_defaults(func=cmd_resolve)

    a = sub.add_parser("apply",
                       help="fold a decisions JSON back into the registry")
    a.add_argument("--registry", default="canonical_tags.json",
                   help="registry JSON to update (default canonical_tags.json).")
    a.add_argument("--decisions", required=True,
                   help="tag_review entities decisions.json export.")
    a.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
