#!/usr/bin/env python3
"""Known-item search battery for `lit search`.

The module ships no list of queries: write your own and pass it with `--items PATH`.
The file is JSON, one entry per query:

  {"items": [
    {"id": "q01",
     "query": "first direct detection gravitational waves binary black hole merger",
     "kind": "arxiv",
     "heldout": false,
     "target": {"title": "Observation of Gravitational Waves from a Binary Black Hole Merger",
                "year": 2016, "arxiv": "1602.03837"}}
  ]}

`query` is topic words, not the title. `kind` is a label of your own; the summary groups by it.
`target` needs a `title` and may carry ids: `arxiv`, `doi`, `lw` (a LessWrong post id) and
`openreview` (a list of note ids). Set `"heldout": true` on the items you keep out of tuning.

A tool "finds" the target at rank k if result k carries one of the target's ids
(arXiv, DOI, OpenReview, LessWrong) or a title that matches the target's title.

Only lit is needed. As an optional comparison, `--tools lit,research` runs a
second search command on the same queries. It is used only when a `research`
command is on PATH; that command must accept
`research search -s papers -n N --json QUERY` and print
{"results": [{"url": ..., "title": ...}, ...]}.

Usage:
  battery/run.py --items my_items.json                        # development set (items not held out)
  battery/run.py --items my_items.json --set heldout          # the held-out items
  battery/run.py --items my_items.json --set all --out results.json
  battery/run.py --items my_items.json --tools lit,research   # with the optional comparison
"""
import argparse
import difflib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from litlib.text import title_key  # noqa: E402

LIT = [sys.executable, os.path.join(ROOT, "lit.py")]


def run(cmd, timeout=120):
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out, err = p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        out, err = "", "timed out after %ds" % timeout
    return out, err, time.monotonic() - t0


def lit_results(q, n):
    out, err, secs = run(LIT + ["search", q, "--n", str(n), "--json"])
    try:
        hits = json.loads(out)["hits"]
    except (ValueError, KeyError):
        return [], secs, (err.strip().splitlines() or ["no JSON"])[-1]
    res = []
    for h in hits:
        blob = " ".join(str(h.get(k) or "") for k in ("arxiv", "doi", "openreview", "lw", "url", "id"))
        res.append((blob, h.get("title") or ""))
    return res, secs, ""


def research_results(q, n):
    out, err, secs = run(["research", "search", "-s", "papers", "-n", str(n), "--json", q])
    try:
        hits = json.loads(out)["results"]
    except (ValueError, KeyError):
        return [], secs, (err.strip().splitlines() or ["no JSON"])[-1]
    return [(h.get("url") or "", h.get("title") or "") for h in hits], secs, ""


TOOLS = {"lit": lit_results, "research": research_results}


def ids_of(item):
    t = item["target"]
    ids = []
    if t.get("arxiv"):
        ids.append(t["arxiv"].lower())
    if t.get("doi"):
        ids.append(t["doi"].lower())
    for o in t.get("openreview") or []:
        ids.append(o)
    if t.get("lw"):
        ids.append(t["lw"])
    return ids


def matches(item, blob, title):
    b = blob.lower()
    for i in ids_of(item):
        il = i.lower()
        # arXiv ids must not match a longer id (1602.03837 vs 1602.038371).
        if re.search(r"(?<![\w.])" + re.escape(il) + r"(?![\d])", b):
            return True
    a, c = title_key(item["target"]["title"]), title_key(title)
    if not a or not c:
        return False
    if a == c:
        return True
    return difflib.SequenceMatcher(None, a, c).ratio() >= 0.92


def rank_of(item, results):
    for k, (blob, title) in enumerate(results, 1):
        if matches(item, blob, title):
            return k
    return None


def summarise(rows, tool):
    rs = [r[tool]["rank"] for r in rows]
    n = len(rs)
    lat = [r[tool]["seconds"] for r in rows]

    def at(k):
        return sum(1 for x in rs if x and x <= k)
    return {"n": n, "r@1": at(1), "r@5": at(5), "r@10": at(10),
            "median_s": round(statistics.median(lat), 2) if lat else None,
            "errors": sum(1 for r in rows if r[tool]["error"])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--items", required=True, metavar="PATH",
                    help="JSON file with your known-item queries (the format is described above)")
    ap.add_argument("--set", choices=["dev", "heldout", "all"], default="dev")
    ap.add_argument("--tools", default="lit",
                    help="comma-separated: lit, and research as an optional comparison (default: lit)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--only", help="comma-separated item ids")
    ap.add_argument("--out", help="write per-query results as JSON here")
    a = ap.parse_args()
    try:
        with open(a.items, encoding="utf-8") as f:
            items = json.load(f)["items"]
    except (OSError, ValueError) as e:
        ap.error("can't read the items file %s: %s" % (a.items, e))
    except (KeyError, TypeError):
        ap.error('the items file %s has no "items" list (the format is in --help)' % a.items)
    if a.set == "dev":
        items = [i for i in items if not i.get("heldout")]
    elif a.set == "heldout":
        items = [i for i in items if i.get("heldout")]
    if a.only:
        want = set(a.only.split(","))
        items = [i for i in items if i["id"] in want]
    tools = []
    for t in (x.strip() for x in a.tools.split(",")):
        if not t or t in tools:
            continue
        if t not in TOOLS:
            ap.error("unknown tool %r; choose from: %s" % (t, ", ".join(TOOLS)))
        if t == "research" and not shutil.which("research"):
            print("research: no such command on PATH; running without the comparison", file=sys.stderr)
            continue
        tools.append(t)
    if not tools:
        ap.error("no tool to run")
    rows = []
    for it in items:
        row = {"id": it["id"], "kind": it["kind"], "query": it["query"], "title": it["target"]["title"]}
        for t in tools:
            res, secs, err = TOOLS[t](it["query"], a.n)
            row[t] = {"rank": rank_of(it, res), "seconds": round(secs, 2), "error": err,
                      "top": [x[1][:70] for x in res[:3]]}
        rows.append(row)
        print("%-6s %-10s %s" % (it["id"], it["kind"],
                                 "  ".join("%s=%s" % (t, row[t]["rank"] or "-") for t in tools)),
              file=sys.stderr, flush=True)
    print("\nSet: %s (%d queries), top %d" % (a.set, len(rows), a.n))
    print("%-9s %5s %5s %5s %5s %9s %6s" % ("tool", "n", "R@1", "R@5", "R@10", "median s", "errors"))
    summ = {}
    for t in tools:
        s = summarise(rows, t)
        summ[t] = s
        print("%-9s %5d %5d %5d %5d %9s %6d" % (t, s["n"], s["r@1"], s["r@5"], s["r@10"], s["median_s"], s["errors"]))
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    print("\nR@10 by kind:")
    for k, rs in sorted(by_kind.items()):
        print("  %-11s n=%-3d %s" % (k, len(rs), "  ".join(
            "%s=%d" % (t, sum(1 for r in rs if r[t]["rank"] and r[t]["rank"] <= 10)) for t in tools)))
    print("\nMisses and differences (rank, '-' = not in top %d):" % a.n)
    for r in rows:
        ranks = [r[t]["rank"] for t in tools]
        if any(x is None or x > 1 for x in ranks):
            print("  %-6s %s  %s" % (r["id"], "  ".join("%s=%-2s" % (t, r[t]["rank"] or "-") for t in tools),
                                     r["title"][:60]))
            for t in tools:
                if r[t]["error"]:
                    print("         %s error: %s" % (t, r[t]["error"][:100]))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"set": a.set, "n": a.n, "summary": summ, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
