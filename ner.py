#!/usr/bin/env python3
# Extract named entities (People / Org / Location) from work1_generic.jsonl.
#
# Flow: load JSONL -> keep only output.answer -> regex pre-pass (structure-based,
# case-insensitive) -> spaCy NER (English primary, hu/de optional) -> fold
# (case + diacritics) dedup -> grouped stdout + named_entities.jsonl.

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict

import spacy

DEFAULT_INPUT = ".workspace/work1_generic.jsonl"
DEFAULT_OUTPUT = "named_entities.jsonl"
DEFAULT_MODEL = "en_core_web_lg"

LIST_CONTEXT = (
    "participant", "attendee", "presenter", "contact",
    "roster", "member list", "chat list", "chats list",
    "list of friends", "list of people", "names:", "names,",
    "name label", "contacts", "attendees", "presenters", "participants",
)

NON_NAME = {
    "sap", "btp", "teams", "office", "chat", "chats", "list", "names",
    "account", "accounts", "admin", "administrator", "meta", "ai", "devops",
    "team", "lead", "the", "a", "an", "of", "with", "and", "or", "to",
    "on", "in", "at", "from", "for", "that", "this", "who", "shown",
    "camera", "mic", "more", "share", "leave", "view", "views", "notes",
    "note", "raise", "react", "annotate", "short", "calendar", "chrome",
    "apps", "app", "etc", "pop", "out", "people", "tab", "tabs", "menu",
    "bar", "button", "click", "icon", "pane", "panel", "row", "column",
    "columns", "label", "section", "page", "screen", "edit", "window",
    "windows", "calls", "call", "help", "settings", "dial", "pad",
    "hold", "solar", "bluetooth", "wifi", "wi-fi",
    "safari", "firefox", "edge", "excel", "word", "photos", "facetime",
    "maps", "onenote", "terminal", "hand", "hands", "up", "down",
    "tile", "tiles", "strip", "bottom", "top", "left", "right", "under",
    "above", "highlighted", "selected", "outlined", "blue", "waving",
    "headphones", "bookshelf", "room", "sixth", "small",
    "transfer", "drafts", "favorites", "recap", "discover", "launchpad",
    "onedrive", "icloud", "messages", "mail", "new",
    "reply", "forward", "send", "save", "open", "close",
     "quick", "control", "take", "join", "mute", "unmute",
     "meeting", "meetings", "gallery", "grid", "pinned",
     "github", "gigya",
}

_NAME_TOKENS = re.compile(
    r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:['\u2019-][A-Za-zÀ-ÖØ-öø-Ÿ]+)*"
)

_SUFF = re.compile(r"\s*[–—-]\s+[A-Za-z].*|\s*\(.*$")
_PUNCT = re.compile(r"""[\u201c\u201d\u201e\u2018\u2019'\"\*\[\]():;`\u2026]+""")
_TRAILING = re.compile(r"\s+e\.g\.$|\s+\.+|^\s*e\.g\.")
_TIME = re.compile(
    r"\s+(?:\d{1,2}:\d{2}(?::\d{2})?|\d{1,2}\.\d{1,2}\.\d{1,4}|"
    r"\d{4}\.\d{2}\.\d{2}|\d{4})")


def has_diacritic(s):
        return any("À" <= ch <= "ſ" for ch in s)


def pick_display(variants):
        counts = Counter(variants)

        def score(v):
            return (1 if has_diacritic(v) else 0,
                     0 if v.isupper() else 1,
                    counts[v])

        return max(variants, key=score)


def fold(text):
    decomposed = unicodedata.normalize("NFD", text)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return base.lower()


def norm_key(tokens):
    kept = [fold(t) for t in tokens
            if fold(t) and fold(t) not in NON_NAME
            and (len(t) > 1 or has_diacritic(t))]
    # 2-token names are order-ambiguous (Hungarian Family,Given vs Western
    # Given Family), so key on the sorted pair. 3+ token names keep order.
    if len(kept) == 2:
        kept = sorted(kept)
    return tuple(kept)


def clean_name(tokens):
        out = []
        for t in tokens:
            f = fold(t)
            if not f or f in NON_NAME or f.isdigit():
                continue
            if len(t) < 2 and not has_diacritic(t):
                continue
            if len(t) == 2 and t.isupper() and not has_diacritic(t):
                continue
            out.append(t)
        return out


def strip_suffix(part):
        part = _SUFF.sub("", part)
        part = _PUNCT.sub("", part)
        part = _TRAILING.sub("", part)
        part = _TIME.sub("", part)
        return part.strip(" \t.*–—-").strip()


def clean_entity_text(text):
        text = text.replace("\n", " ")
        text = text.replace("/", " ")
        text = _PUNCT.sub("", text)
        text = text.replace(",", " ")
        text = _TRAILING.sub("", text)
        text = _TIME.sub(" ", text)
        text = re.sub(r"^\s*(the|a|an)\s+", "", text, flags=re.IGNORECASE)
        return " ".join(text.split())


def has_digit_noise(text):
        return re.search(r"\d", text) is not None


def candidates_in_line(line):
        lowered = line.lower()
        if "initials" in lowered:
            return
        if not any(k in lowered for k in LIST_CONTEXT):
            return
        candidates = []
        for chunk in re.split(r"[;]+", line):
            parts = [strip_suffix(p) for p in re.split(r"\s*,\s*", chunk.strip())]
            parts = [p for p in parts if p]
            if not parts:
                continue
            if len(parts) == 1:
                cands = [parts[0]]
            elif all(" " not in p for p in parts):
                cands = [" ".join(reversed(parts))]
            else:
                cands = parts
            for c in cands:
                toks = clean_name(c.split())
                if not (1 <= len(toks) <= 3) or fold(toks[0]) in NON_NAME:
                    continue
                if not any(any(ch.isupper() for ch in t) or has_diacritic(t)
                           for t in toks):
                    continue
                candidates.append(" ".join(toks))
        if len(candidates) < 2:
            return
        for c in candidates:
            yield c


def to_record(variants, count, kind):
    return {
        "type": kind,
        "display": pick_display(variants),
        "variants": sorted(variants),
        "count": count,
    }


def iter_persons(texts):
    bucket = defaultdict(lambda: {"variants": set(), "count": 0})
    for text in texts:
        for line in text.splitlines():
            for cand in candidates_in_line(line):
                cand = cand.strip()
                key = norm_key(cand.split())
                if len(key) < 1:
                    continue
                entry = bucket[key]
                entry["variants"].add(cand)
                entry["count"] += 1
    return [to_record(e["variants"], e["count"], "Person")
            for e in bucket.values()]


def _chunks(text, size=50000):
    for i in range(0, len(text), size):
        yield text[i:i + size]


def iter_spacy_long(texts, model="en_core_web_lg"):
    try:
        nlp = spacy.load(model)
    except Exception as exc:
        print("warning: cannot load model %r (%s); skipping spaCy"
               % (model, exc), file=sys.stderr)
        return []
    want = {"ORG", "PER", "PERSON", "GPE", "LOC", "FAC"}
    bucket = defaultdict(lambda: {"type": "", "variants": set(), "count": 0})
    for text in texts:
        for chunk in _chunks(text):
            for ent in nlp(chunk).ents:
                if ent.label_ not in want or len(ent.text.strip()) < 2:
                    continue
                cls = ("Person" if ent.label_ in {"PER", "PERSON"}
                       else "Location" if ent.label_ in {"GPE", "LOC", "FAC"}
                       else "Org")
                cleaned = clean_entity_text(ent.text)
                if not cleaned or has_digit_noise(cleaned):
                    continue
                entry = bucket[(cls, fold(cleaned))]
                entry["type"] = cls
                entry["variants"].add(cleaned)
                entry["count"] += 1
    return [to_record(e["variants"], e["count"], e["type"])
            for e in bucket.values()]


def merge_people(groups):
    bucket = defaultdict(lambda: {"variants": set(), "count": 0})
    for e in groups:
        if e["type"] != "Person":
            continue
        toks = _NAME_TOKENS.findall(e["display"])
        key = norm_key(toks)
        if not key:
            continue
        entry = bucket[key]
        entry["variants"].update(e["variants"])
        entry["count"] += e["count"]
    return [to_record(e["variants"], e["count"], "Person")
            for e in bucket.values()]


def load_answers(path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out = rec.get("output")
        if isinstance(out, dict) and out.get("answer"):
            yield out["answer"]


ORGLOC_NOISE = {
    "ui", "sql", "cdc", "tb", "kb", "cpu", "vm", "gpu", "ssh", "srv",
    "ai", "bc", "ar", "ns", "pa", "uk", "gb", "us", "hu", "huf", "gib",
    "mac", "finder", "map", "maps", "chrome", "safari", "edge",
    "firefox", "terminal", "launchpad", "onedrive", "icloud",
    "teams", "office", "bar", "tab", "tabs", "menu", "panel", "pane",
    "row", "column", "columns", "label", "section", "page", "screen",
    "window", "windows", "button", "icon", "grid", "tile", "tiles",
    "strip", "view", "views", "more", "share", "leave", "call", "calls",
    "meeting", "meetings", "gallery", "recap", "discover", "favorites",
    "drafts", "transfer", "control", "quick", "edit", "new", "reply",
    "forward", "send", "save", "open", "close", "join", "mute", "unmute",
    "hold", "top", "bottom", "left", "right", "under", "above", "blue",
}


def keep_orgloc(e):
    if len(e["display"]) < 2:
        return False
    toks = e["display"].split()
    if len(toks) == 1 and fold(toks[0]) in ORGLOC_NOISE:
        return False
    return True


PERSON_NOISE = {
      "microsoft", "teams", "chrome", "macos", "mac", "safari", "edge",
      "firefox", "office", "windows", "sap", "gigya", "github", "apple",
      "azure", "powershell", "ssms", "aws", "google", "one", "note",
      "facetime", "maps", "calendar", "terminal", "dock", "launchpad",
      "chat", "view", "edit", "window", "help", "meeting",
      "facebook", "datadog", "joule", "confluence", "btp", "kyma", "shell",
      "shell", "kb", "engage", "home", "archive", "resource", "engage",
      "raise", "react", "engage",
}


def keep_person(e):
    toks = [fold(t) for t in e["display"].split()]
    real = [t for t in toks if t and t not in PERSON_NOISE
               and t not in NON_NAME and not t.isdigit()]
    return len(real) >= 1


def main():
    ap = argparse.ArgumentParser(description="Extract People/Org/Location entities.")
    ap.add_argument("input", nargs="?", default=DEFAULT_INPUT)
    ap.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="primary spaCy model (default en_core_web_lg)")
    ap.add_argument("--language",
                    help="optional extra model to also run (e.g. hu_core_news_lg)")
    args = ap.parse_args()

    texts = list(load_answers(args.input))
    print("loaded %d answers" % len(texts), file=sys.stderr)

    regex_people = iter_persons(texts)
    spacy_primary = iter_spacy_long(texts, args.model)
    spacy_orgloc = [e for e in spacy_primary if e["type"] != "Person"]
    spacy_people = [e for e in spacy_primary if e["type"] == "Person"]

    people = merge_people(regex_people + spacy_people)
    orgs = [e for e in spacy_orgloc if e["type"] == "Org"]
    locs = [e for e in spacy_orgloc if e["type"] == "Location"]

    if args.language:
        extra = iter_spacy_long(texts, args.language)
        people = merge_people(people + [e for e in extra if e["type"] == "Person"])
        for e in extra:
            if e["type"] == "Org":
                orgs.append(e)
            elif e["type"] == "Location":
                locs.append(e)

    orgs = [e for e in orgs if keep_orgloc(e)]
    locs = [e for e in locs if keep_orgloc(e)]
    people = [e for e in people if keep_person(e)]

    person_keys = {norm_key(_NAME_TOKENS.findall(e["display"])) for e in people}

    def is_person(e):
        toks = _NAME_TOKENS.findall(e["display"])
        return bool(toks) and norm_key(toks) in person_keys

    demoted = [e for e in orgs + locs if is_person(e)]
    orgs = [e for e in orgs if not is_person(e)]
    locs = [e for e in locs if not is_person(e)]
    if demoted:
        people = merge_people(
            people + [{**e, "type": "Person"} for e in demoted])

    entities = sorted(
        people + orgs + locs,
        key=lambda x: (-x["count"], x["type"].lower(), x["display"].lower()),
     )

    for kind in ("Person", "Org", "Location"):
        items = [e for e in entities if e["type"] == kind]
        label = {"Person": "People:", "Org": "Org:", "Location": "Location:"}[kind]
        print(label)
        for e in items:
            extra = ("       (" + ", ".join(e["variants"][1:]) + ")")
            suffix = extra if len(e["variants"]) > 1 else ""
            print("       %s        [%d]%s" % (e["display"], e["count"], suffix))

    with open(args.output, "w", encoding="utf-8") as f:
        for e in entities:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print("wrote %d entities to %s" % (len(entities), args.output),
          file=sys.stderr)


if __name__ == "__main__":
    main()
