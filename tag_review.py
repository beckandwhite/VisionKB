#!/usr/bin/env python3
"""Human-in-the-loop TAG review for work6 n-gram candidates.

Two subcommands, stdlib-only, dataset-scoped like work5/work6:

  build   read work6_tag_candidates.jsonl -> emit a self-contained HTML page
          plus a sidecar candidates JSON. Open the HTML; flip each row to
          keep / drop / leave pending; click Export to download a decisions JSON.

  apply   read that decisions JSON (the export carries ngram/count/n/decision)
          and emit a clean, de-duplicated TAG_LIST. The candidates produced by
          work6 are already case/diacritic-folded lower-case, so apply dedups on
          the folded form and renders space-separated multiword grams as
          hyphenated tags to match the canonical TAG_LIST style.

`build` prunes the 100k+ candidate rows to the top-N of each n (--per-n,
default 150) so a human can actually review a page. Nothing is dropped by noise
category: a high-occurrence token is a candidate simply because it is common, so
the keep/drop call is left entirely to the reviewer.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter

DEFAULT_CANDIDATES = "work6_tag_candidates.jsonl"


def fold(text):
    """NFD fold case + diacritics so macos/MacOS / Tamas/Tamás collapse."""
    decomposed = unicodedata.normalize("NFD", text)
    base = "".join(ch for ch in decomposed
                   if unicodedata.category(ch) != "Mn")
    return base.lower()


def to_tag(ngram):
    """Render a folded n-gram as a TAG_LIST token: spaces -> hyphens."""
    return re.sub(r"\s+", "-", ngram.strip()).lower()


def load_candidates(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "ngram" in rec and "count" in rec:
                row = dict(rec)
                row["n"] = int(row.get("n", 1))
                rows.append(row)
    rows.sort(key=lambda r: (-r["count"], r["n"], r["ngram"].lower()))
    return rows


def top_per_n(rows, per_n):
    by_n = {}
    for row in rows:
        by_n.setdefault(row["n"], []).append(row)
    out = []
    for gram in sorted(by_n):
        out.extend(by_n[gram][:per_n])
    out.sort(key=lambda r: (-r["count"], r["n"], r["ngram"].lower()))
    return out


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build_html(data):
    return HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))


def cmd_build(args):
    rows = load_candidates(args.candidates)
    pruned = top_per_n(rows, args.per_n)
    data = [{"id": i, "ngram": r["ngram"], "count": r["count"], "n": r["n"]}
             for i, r in enumerate(pruned)]
    out_html = args.html or "tag_review.html"
    out_json = out_html + ".candidates.jsonl"
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(build_html(data))
    with open(out_json, "w", encoding="utf-8") as fh:
        for i, r in enumerate(pruned):
            fh.write(json.dumps({"id": i, **r}, ensure_ascii=False) + "\n")
    per_n = Counter(r["n"] for r in pruned)
    print("build: %d rows (n1=%d n2=%d n3=%d) -> %s"
          % (len(pruned), per_n[1], per_n[2], per_n[3], out_html))
    print("  open %s, flip keep/drop, Export -> tag_review.decisions.json"
          % out_html)
    print("  then apply: python3 tag_review.py apply --decisions "
          "tag_review.decisions.json")
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def cmd_apply(args):
    with open(args.decisions, encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        payload = payload.get("decisions") or []
    keep = [e for e in payload if str(e.get("decision", "keep")).lower() == "keep"]
    drop = [e for e in payload if str(e.get("decision", "drop")).lower() == "drop"]

    seen = set()
    tags = []
    for e in sorted(keep, key=lambda x: -int(x.get("count", 0))):
        tag = to_tag(e["ngram"])
        key = fold(tag)
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)

    sys.stderr.write("apply: %d keep, %d drop, %d unique tags\n"
                      % (len(keep), len(drop), len(tags)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for tag in tags:
                fh.write(tag + "\n")
        sys.stderr.write("wrote %d tags to %s\n" % (len(tags), args.out))
    else:
        print(" ".join(tags))
    return 0


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>TAG candidate review</title>
<style>
 :root { --keep:#1a7f37; --drop:#cf222e; --pending:#57606a; }
 * { box-sizing: border-box; }
 body { font: 13px/1.4 -apple-system, system-ui, sans-serif; margin:0;
        background:#f6f8fa; color:#1f2328; }
 header { position:sticky; top:0; background:#fff; padding:10px 16px;
          border-bottom:1px solid #d0d7de; display:flex; gap:8px;
          align-items:center; flex-wrap:wrap; z-index:10; }
 header b { font-size:14px; }
 .stats { color:var(--pending); }
 button { font:inherit; padding:4px 10px; border:1px solid #d0d7de;
           border-radius:6px; background:#fff; cursor:pointer; }
 button:hover { background:#f3f4f6; }
 input, select { font:inherit; padding:4px 8px; border:1px solid #d0d7de;
                  border-radius:6px; }
 table { border-collapse:collapse; width:100%; background:#fff; }
 th, td { text-align:left; padding:4px 8px; border-bottom:1px solid #eaeef2; }
 th { position:sticky; top:60px; background:#f6f8fa; font-size:12px; }
 tr[data-decision="keep"] { background:#e6f4ea; }
 tr[data-decision="drop"] { background:#fbe9ea; color:#8b2c2c; }
 .n { width:28px; color:var(--pending); text-align:center; }
 .c { width:64px; text-align:right; font-variant-numeric:tabular-nums; }
 .g { font-family:ui-monospace, monospace; }
 .badge { font-weight:700; padding:1px 6px; border-radius:10px; color:#fff; }
 .b1 { background:#0969da; } .b2 { background:#8250df; } .b3 { background:#1a7f37; }
 .act button { padding:2px 8px; font-size:12px; margin-right:4px; }
 #empty { padding:24px; color:var(--pending); }
</style>
</head>
<body>
<header>
    <b>TAG candidate review</b>
    <input id="q" placeholder="filter text…" />
    <select id="n"><option value="all">all n</option>
      <option value="1">1-grams</option>
      <option value="2">2-grams</option>
      <option value="3">3-grams</option></select>
    <select id="dec"><option value="pending">pending</option>
      <option value="keep">keep</option>
      <option value="drop">drop</option></select>
    <span class="stats" id="stats"></span>
    <span style="flex:1"></span>
    <button id="export">Export decisions</button>
    <button id="keep-visible">Mark visible keep</button>
</header>
<table>
    <thead><tr><th class="n">n</th><th class="c">count</th>
      <th>candidate</th><th>decision</th></tr></thead>
    <tbody id="body"></tbody>
</table>
<div id="empty">Nothing matches the filter.</div>
<script>
var DATA = __DATA__;
var decisions = {};
try {
   var saved = JSON.parse(localStorage.getItem("tag_review") || "{}");
   if (saved.decisions) decisions = saved.decisions;
   if (saved.q) document.getElementById("q").value = saved.q;
} catch (e) {}
function dec(id){ return decisions[id] || "pending"; }
function persist(){
   localStorage.setItem("tag_review", JSON.stringify({
      decisions: decisions,
      q: document.getElementById("q").value,
      n: document.getElementById("n").value,
      dec: document.getElementById("dec").value
   }));
}
function esc(s){ return s.replace(/[&<>"']/g, function(c){
   return {'"':'&quot;','&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;'}[c]; }); }
function matches(r){
   var q = document.getElementById("q").value.toLowerCase();
   var n = document.getElementById("n").value;
   var d = document.getElementById("dec").value;
   if (n !== "all" && String(r.n) !== n) return false;
   if (d === "keep" && dec(r.id) !== "keep") return false;
   if (d === "drop" && dec(r.id) !== "drop") return false;
   if (d === "pending" && dec(r.id) !== "pending") return false;
   if (q && (r.ngram + " " + r.count).toLowerCase().indexOf(q) === -1) return false;
   return true;
}
function refreshStats(){
   var keep=0, drop=0;
   for (var i=0;i<DATA.length;i++){
      var d = dec(DATA[i].id);
      if (d === "keep") keep++; else if (d === "drop") drop++;
   }
   document.getElementById("stats").textContent =
      DATA.length + " rows · keep " + keep + " · drop " + drop;
}
function render(){
   var body = document.getElementById("body");
   body.innerHTML = "";
   var shown = 0;
   DATA.forEach(function(r){
      if (!matches(r)) return;
      shown++;
      var tr = document.createElement("tr");
      tr.id = "row_" + r.id;
      tr.dataset.decision = dec(r.id);
      var ncls = "b" + Math.min(r.n, 3);
      var act = ['<button data-id="'+r.id+'" data-v="keep">keep</button>',
                 '<button data-id="'+r.id+'" data-v="drop">drop</button>',
                '<button data-id="'+r.id+'" data-v="pending">'
                   + (dec(r.id) ? "clear" : "•") + '</button>'].join("");
      tr.innerHTML =
           '<td class="n">'+r.n+'</td>'
         + '<td class="c">'+r.count+'</td>'
         + '<td class="g">'+esc(r.ngram)
             + ' <span class="badge '+ncls+'">n'+r.n+'</span></td>'
         + '<td>'+ act + '</td>';
      body.appendChild(tr);
   });
   document.getElementById("empty").style.display = shown ? "none" : "block";
   refreshStats();
}
function setDec(id, v, clear){
   if (clear) delete decisions[id];
   else decisions[id] = v;
   var tr = document.getElementById("row_" + id);
   if (tr) tr.dataset.decision = v;
   refreshStats(); persist();
}
document.addEventListener("click", function(e){
   var t = e.target;
   if (t.dataset && t.dataset.id !== undefined){
      var id = t.dataset.id, v = t.dataset.v;
      setDec(id, v, v === "pending");
   }
});
document.getElementById("export").onclick = function(){
   var out = DATA.map(function(r){
      var d = dec(r.id);
      if (!d) return null;
      return {id: r.id, ngram: r.ngram, count: r.count, n: r.n, decision: d};
   }).filter(Boolean);
   var blob = new Blob([JSON.stringify(out, null, 2)],
                       {type: "application/json"});
   var url = URL.createObjectURL(blob);
   var a = document.createElement("a");
   a.href = url; a.download = "tag_review.decisions.json";
   document.body.appendChild(a); a.click(); document.body.removeChild(a);
   URL.revokeObjectURL(url);
   persist();
};
document.getElementById("keep-visible").onclick = function(){
   DATA.forEach(function(r){ if (matches(r)){ decisions[r.id] = "keep"; } });
   persist(); render();
};
document.getElementById("q").oninput = render;
document.getElementById("n").onchange = function(){ persist(); render(); };
document.getElementById("dec").onchange = function(){ persist(); render(); };
render();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# entities — review the work7 AKA queue + type overrides
# ---------------------------------------------------------------------------
#
# Surfaces the work7 ``canonical_tags.json`` registry: each entity with its
# proposed aka (the heuristic queue: fuzzy / structural / cooccur) and its
# engine-recommended ``type``.  The human confirms/drops AKA and overrides the
# type; Export -> tag_review.entity.decisions.json, which ``work7 apply`` folds
# back into the registry (confirm -> active human AKA; drop -> deprecated;
# type -> set).   This is the "bring knowledge in over time" step.
#
# The review shows proposals first (most evidence), so the human confirms the
# highest-value links; confirmed forms become active tags on the next resolve.

ENT_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Entity + AKA review (work7)</title><style>
 :root { --keep:#1a7f37; --drop:#cf222e; --pending:#57606a; }
 * { box-sizing: border-box; }
 body { font:13px/1.4 -apple-system,system-ui,sans-serif;margin:0;
        background:#f6f8fa;color:#1f2328; }
 header { position:sticky;top:0;background:#fff;padding:10px 16px;
          border-bottom:1px solid #d0d7de;display:flex;gap:8px;align-items:center;
             flex-wrap:wrap;z-index:10; }
 header b { font-size:14px; } .stats { color:var(--pending); }
 button { font:inherit;padding:4px 10px;border:1px solid #d0d7de;border-radius:6px;
          background:#fff;cursor:pointer; } button:hover { background:#f3f4f6; }
 input,select { font:inherit;padding:4px 8px;border:1px solid #d0d7de;border-radius:6px; }
 table { border-collapse:collapse;width:100%;background:#fff; }
 th,td { text-align:left;padding:4px 8px;border-bottom:1px solid #eaeef2;vertical-align:top; }
 th { position:sticky;top:60px;background:#f6f8fa;font-size:12px; }
 tr[data-decision="confirm"] { background:#e6f4ea; }
 tr[data-decision="drop"] { background:#fbe9ea;color:#8b2c2c; }
 .n { width:40px;color:var(--pending);} .c { width:60px;text-align:right;
        font-variant-numeric:tabular-nums; }
 .g { font-family:ui-monospace,monospace; }
 .badge { font-weight:700;padding:1px 6px;border-radius:10px;color:#fff; }
 .b1 { background:#0969da; } .b2 { background:#8250df; }
 .act button { padding:2px 8px;font-size:12px;margin-right:4px; }
 .evidence { color:#57606a;font-family:ui-monospace,monospace;font-size:11px; }
 .type { font-weight:600; }
 #empty { padding:24px;color:var(--pending); }
</style></head><body>
<header>
   <b>Entity + AKA review (work7)</b>
   <input id="q" placeholder="filter canonical / alias…" />
   <select id="rel"><option value="all">all relations</option>
     <option value="aka">aka</option><option value="member">member</option>
     <option value="identity">identity</option></select>
   <select id="dec"><option value="pending">pending</option>
     <option value="confirm">confirm</option><option value="drop">drop</option></select>
   <span class="stats" id="stats"></span>
   <span style="flex:1"></span>
   <button id="export">Export decisions</button>
   <button id="confirm-visible">Confirm visible</button>
</header>
<table><thead><tr>
   <th class="c">occ</th><th type></th><th>canonical + AKA</th>
   <th>relation</th><th>decision</th></tr></thead>
   <tbody id="body"></tbody></table>
   <div id="empty">Nothing matches the filter.</div>
<script>
var TYPES = ["Person","Place","Group","Product","Scene","Concept","Other"];
var DATA = __DATA__;
var decisions = {};
try {
   var saved = JSON.parse(localStorage.getItem("tag_review_entities") || "{}");
   if (saved.decisions) decisions = saved.decisions;
   if (saved.q) document.getElementById("q").value = saved.q;
} catch (e) {}
function dec(id){ return (id in decisions) ? decisions[id] : "pending"; }
function persist(){
   localStorage.setItem("tag_review_entities", JSON.stringify({
      decisions: decisions,
      q: document.getElementById("q").value,
      rel: document.getElementById("rel").value,
      dec: document.getElementById("dec").value
   }));
}
function esc(s){ return String(s).replace(/[&<>"']/g, function(c){
   return {'"':'&quot;','&':'&amp;','<':'&lt;','>':'&gt;','\'':'&#39;'}[c]; }); }
function matches(r){
   var q = document.getElementById("q").value.toLowerCase();
   var rel = document.getElementById("rel").value;
   var d = document.getElementById("dec").value;
   if (rel !== "all" && r.relation !== rel) return false;
   if (d === "confirm" && dec(r.id) !== "confirm") return false;
   if (d === "drop" && dec(r.id) !== "drop") return false;
   if (d === "pending" && dec(r.id) !== "pending") return false;
   if (q && ((r.canonical + " " + r.alias + " " + (r.evidence||"")).
             toLowerCase().indexOf(q) === -1)) return false;
   return true;
}
function refreshStats(){
   var conf=0, drop=0;
   for (var i=0;i<DATA.length;i++){
     var d = dec(DATA[i].id);
     if (d === "confirm") conf++; else if (d === "drop") drop++;
   }
   document.getElementById("stats").textContent =
     DATA.length + " proposals · confirm " + conf + " · drop " + drop;
}
function render(){
   var body = document.getElementById("body");
   body.innerHTML = "";
   var shown = 0;
   DATA.forEach(function(r){
     if (!matches(r)) return;
     shown++;
     var tr = document.createElement("tr");
     tr.id = "row_" + r.id;
     tr.dataset.decision = dec(r.id);
     var act = ['<button data-id="'+r.id+'" data-v="confirm">confirm</button>',
                '<button data-id="'+r.id+'" data-v="drop">drop</button>',
                '<button data-id="'+r.id+'" data-v="pending">'
                   + (dec(r.id) ? "clear" : "•") + '</button>'].join("");
     tr.innerHTML =
           '<td class="c">'+r.count+'</td>'
         + '<td class="type">'+esc(r.type)+'</td>'
         + '<td class="g">'+esc(r.canonical)+'  \u2190  '+esc(r.alias)
            + (r.evidence ? '<div class="evidence">'+esc(r.evidence)+'</div>' : '')
            + '</td>'
         + '<td>'+esc(r.relation)+'</td>'
         + '<td>'+ act + '</td>';
     body.appendChild(tr);
   });
   document.getElementById("empty").style.display = shown ? "none" : "block";
   refreshStats();
}
function setDec(id, v, clear){
   if (clear) delete decisions[id];
   else decisions[id] = v;
   var tr = document.getElementById("row_" + id);
   if (tr) tr.dataset.decision = v;
   refreshStats(); persist();
}
document.addEventListener("click", function(e){
   var t = e.target;
   if (t.dataset && t.dataset.id !== undefined){
     var id = t.dataset.id, v = t.dataset.v;
     setDec(id, v, v === "pending");
   }
});
document.getElementById("export").onclick = function(){
   var out = DATA.map(function(r){
     var d = dec(r.id);
     if (!d) return null;
     return {entity_id: r.entity_id, entity_type: r.type,
            alias: r.alias, decision: d, relation: r.relation,
            evidence: r.evidence};
   }).filter(Boolean);
   var blob = new Blob([JSON.stringify(out, null, 2)],
                       {type: "application/json"});
   var url = URL.createObjectURL(blob);
   var a = document.createElement("a");
   a.href = url; a.download = "tag_review.entity.decisions.json";
   document.body.appendChild(a); a.click(); document.body.removeChild(a);
   URL.revokeObjectURL(url); persist();
};
document.getElementById("confirm-visible").onclick = function(){
   DATA.forEach(function(r){ if (matches(r)){ decisions[r.id] = "confirm"; } });
   persist(); render();
};
document.getElementById("q").oninput = render;
document.getElementById("rel").onchange = function(){ persist(); render(); };
document.getElementById("dec").onchange = function(){ persist(); render(); };
render();
</script></body></html>
"""


def cmd_entities(args):
    path = args.registry
    if not os.path.isdir(path) and not os.path.isfile(path):
        print("registry not found: %s" % path, file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as fh:
        registry = json.load(fh)
    proposals = []
    for e in registry:
        for q in e.get("proposed_aka", []):
            proposals.append({
                 "entity_id": e.get("id"),
                 "canonical": e.get("canonical"),
                 "type": e.get("type", "Other"),
                 "alias": q.get("form"),
                 "relation": q.get("relation", "aka"),
                 "count": q.get("count", 0),
                 "evidence": q.get("evidence", ""),
            })
    proposals.sort(key=lambda p: (-p["count"], p["entity_id"] or "",
                                  p["alias"].lower()))
    pruned = proposals[:args.limit]
    data = [{"id": i, **r} for i, r in enumerate(pruned)]
    out_html = args.html or "tag_review_entities.html"
    out_json = out_html + ".entities.jsonl"
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(ENT_HTML.replace("__DATA__",
                                  json.dumps(data, ensure_ascii=False)))
    with open(out_json, "w", encoding="utf-8") as fh:
        for i, r in enumerate(pruned):
            fh.write(json.dumps({"id": i, **r}, ensure_ascii=False) + "\n")
    print("entities: %d proposals (%d total) -> %s"
           % (len(pruned), len(proposals), out_html))
    print("  open %s, confirm/drop the AKA queue, Export -> "
            "tag_review.entity.decisions.json" % out_html)
    print("  then apply: python3 work7.py apply --registry %s "
            "--decisions tag_review.entity.decisions.json" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="emit review HTML + candidates JSON")
    b.add_argument("candidates", nargs="?", default=DEFAULT_CANDIDATES)
    b.add_argument("--per-n", type=int, default=150,
                   help="top-N candidates kept per n-gram width (default 150)")
    b.add_argument("--html", default=None,
                   help="output HTML path (default tag_review.html)")
    b.set_defaults(func=cmd_build)

    a = sub.add_parser("apply", help="turn a decisions export into a TAG_LIST")
    a.add_argument("--decisions", required=True,
                   help="the decisions JSON downloaded from the HTML review")
    a.add_argument("--out", default=None,
                   help="write the TAG_LIST to this file "
                         "(one tag per line); default prints to stdout "
                         "(space-joined)")
    a.set_defaults(func=cmd_apply)

    e = sub.add_parser("entities",
                        help="review work7 AKA proposals + type overrides")
    e.add_argument("registry", nargs="?", default=None,
                    help="canonical_tags.json (work7 output); "
                           "default the env's canonical_tags.json.")
    e.add_argument("--limit", type=int, default=400,
                    help="top-N proposals to surface for review (default 400).")
    e.add_argument("--html", default=None,
                    help="output HTML path "
                           "(default tag_review_entities.html).")
    e.set_defaults(func=cmd_entities)

    args = ap.parse_args()
    if args.cmd == "entities" and not args.registry:
        import config_loader
        _env, cfg = config_loader.resolve_environment()
        args.registry = str(cfg["env_dir"] / "canonical_tags.json")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
