#!/usr/bin/env python3
"""lit: find, fetch, read and check ML and AI-safety papers. Run `lit -h`."""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from litlib import doc as D  # noqa: E402
from litlib import library as L  # noqa: E402
from litlib import net  # noqa: E402
from litlib import quote as Q  # noqa: E402
from litlib import refcheck as R  # noqa: E402
from litlib import search as SR  # noqa: E402
from litlib import sources as S  # noqa: E402

EXIT_ERR = 3


def log(msg):
    print("lit: " + msg, file=sys.stderr)


def label(s):
    return (s["num"] + " " + s["title"]).strip()


# ---------------------------------------------------------------- search

def cmd_search(a):
    srcs = [s.strip() for s in a.source.split(",") if s.strip()] if a.source else SR.DEFAULT_SOURCES
    bad = [s for s in srcs if s not in S.SEARCHERS]
    if bad:
        log("unknown source(s): %s; known: %s" % (",".join(bad), ",".join(S.SEARCHERS)))
        return EXIT_ERR
    hits, errors, secs = SR.search(a.query, srcs, n=a.n, year=a.year)
    for s, e in sorted(errors.items()):
        if not (net.QUIET and hits):
            log("%s (carried on without it)" % e)
    if a.json:
        out = []
        for m in hits:
            m = {k: v for k, v in m.items() if k not in ("rank",)}
            m["id"] = SR.display_id(m)
            m["abstract"] = (m.get("abstract") or "")[:600]
            out.append(m)
        print(json.dumps({"query": a.query, "hits": out, "errors": errors,
                          "seconds": round(secs, 2)}, indent=1, ensure_ascii=False))
    else:
        for i, m in enumerate(hits, 1):
            print("%2d. %s" % (i, SR.line(m)))
        if not hits:
            print("no results")
    return 0 if hits else 1


# ---------------------------------------------------------------- get / add / list

def cmd_get(a):
    rc = 0
    for ident in a.ids:
        try:
            key, meta, cached = L.get(ident, log, force=a.force)
        except (L.LitError, net.NetError, RuntimeError) as e:
            log(str(e))
            rc = EXIT_ERR
            continue
        print("%s  %s  %s (%s sections, %s words%s)" % (
            key, meta.get("year") or "----", meta.get("title"), meta.get("n_sections"),
            meta.get("n_words"), ", already in library" if cached else ""))
        print("   text from: %s" % meta.get("fulltext_source"))
    return rc


def cmd_add(a):
    p = os.path.abspath(a.path)
    if not os.path.isfile(p):
        log("no such file: %s" % a.path)
        return EXIT_ERR
    try:
        meta, document = L.get_local(p, a.title, log)
    except (RuntimeError, OSError) as e:
        log(str(e))
        return EXIT_ERR
    for f in ("doi", "arxiv"):
        v = getattr(a, f)
        if v:
            meta[f] = v
    if a.authors:
        meta["authors"] = [x.strip() for x in a.authors.split(";") if x.strip()]
    if a.year:
        meta["year"] = a.year
    key = a.key or L.key_for("local", p, meta["title"])
    meta = L.save(key, meta, document)
    cols = document.get("pdf_two_column_pages")
    print("%s  %s (%d sections, %d words%s)" % (key, meta["title"], meta["n_sections"], meta["n_words"],
                                                 ", %d two-column pages split" % cols if cols else ""))
    return 0


def cmd_list(a):
    ks = L.keys()
    if not ks:
        print("library is empty (%s)" % L.LIB)
        return 0
    for k in ks:
        try:
            m = json.load(open(os.path.join(L.path(k), "metadata.json")))
        except (OSError, ValueError):
            continue
        if a.json:
            print(json.dumps({"key": k, "title": m.get("title"), "year": m.get("year"),
                              "words": m.get("n_words")}))
        else:
            print("%-28s %s  %s  (%s words)" % (k, m.get("year") or "----", (m.get("title") or "")[:80],
                                               m.get("n_words")))
    return 0


def _load(k):
    key = L.find(k)
    meta, document, full = L.load(key)
    return key, meta, document, full


# ---------------------------------------------------------------- toc / read

def cmd_toc(a):
    key, meta, document, _ = _load(a.key)
    print("%s: %s" % (key, meta.get("title")))
    for i, s in enumerate(document["sections"], 1):
        ind = "  " * (max(1, s["level"]) - 1)
        print("%3d  %s%s  (%d words)" % (i, ind, label(s), len(s["text"].split())))
    print("figures: %d  tables: %d" % (len(document.get("figures", [])), len(document.get("tables", []))))
    return 0


def find_section(document, want):
    secs = document["sections"]
    w = want.strip()
    # exact number
    for i, s in enumerate(secs):
        if s["num"] and s["num"] == w.rstrip("."):
            return i
    lw = w.lower()
    for i, s in enumerate(secs):
        if s["title"].lower() == lw or label(s).lower() == lw:
            return i
    for i, s in enumerate(secs):
        if lw in s["title"].lower():
            return i
    # position in the toc ("#7")
    if w.startswith("#") and w[1:].isdigit():
        k = int(w[1:]) - 1
        if 0 <= k < len(secs):
            return k
    return None


def section_span(document, i):
    """Section i plus its subsections."""
    secs = document["sections"]
    lv = secs[i]["level"]
    j = i + 1
    while j < len(secs) and secs[j]["level"] > lv:
        j += 1
    return secs[i:j]


def limit_words(text, n):
    if not n:
        return text
    if len(text.split()) <= n:
        return text
    out, c = [], 0
    for tok in re.split(r"(\s+)", text):
        if tok.strip():
            c += 1
            if c > n:
                break
        out.append(tok)
    return "".join(out).rstrip() + "\n[... cut at %d words; use --max-words to change]" % n


def cmd_read(a):
    key, meta, document, full = _load(a.key)
    out = []
    if a.abstract:
        i = find_section(document, "Abstract")
        if i is not None:
            out.append("## Abstract\n" + document["sections"][i]["text"])
        elif meta.get("abstract"):
            out.append("## Abstract (from metadata)\n" + meta["abstract"])
        else:
            log("no abstract found")
            return EXIT_ERR
    if a.figures:
        figs = document.get("figures") or []
        out.append("\n\n".join("[%s] %s" % (f["section"], f["caption"]) for f in figs) or "no figure captions found")
    if a.tables:
        tabs = document.get("tables") or []
        out.append("\n\n".join("[%s] %s\n%s" % (t["section"], t["caption"], t.get("text", "")) for t in tabs)
                   or "no tables found")
    if a.section:
        i = find_section(document, a.section)
        if i is None:
            log("no section %r; run: lit toc %s" % (a.section, key))
            return EXIT_ERR
        parts = []
        for s in section_span(document, i):
            parts.append("%s %s" % ("#" * max(1, s["level"]), label(s)))
            if s["text"]:
                parts.append(s["text"])
        out.append("\n\n".join(parts))
    if not out:
        out.append(full)
    print(limit_words("\n\n".join(out), a.max_words))
    return 0


# ---------------------------------------------------------------- grep / quote

def section_offsets(full):
    """(offset, label) for each heading line in fulltext.txt."""
    offs = []
    for m in re.finditer(r"^#+ (.*)$", full, re.M):
        offs.append((m.start(), m.group(1)))
    return offs


def section_at(offs, pos):
    lab = ""
    for o, l in offs:
        if o <= pos:
            lab = l
        else:
            break
    return lab


def grep_one(key, full, pat, ctx, max_hits):
    offs = section_offsets(full)
    paras = []
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", full):
        paras.append((m.start(), m.group(0)))
    n = 0
    for start, ptxt in paras:
        if ptxt.startswith("#"):
            continue
        flat = " ".join(ptxt.split())
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])", flat)
        for k, s in enumerate(sents):
            if pat.search(s):
                lo, hi = max(0, k - ctx), min(len(sents), k + ctx + 1)
                text = " ".join(sents[lo:hi])
                text = pat.sub(lambda m: ">>" + m.group(0) + "<<", text)
                print("%s [%s] %s" % (key, section_at(offs, start), text))
                n += 1
                if max_hits and n >= max_hits:
                    return n
    return n


def cmd_grep(a):
    flags = 0 if a.case else re.I
    try:
        pat = re.compile(a.pattern, flags)
    except re.error as e:
        log("bad pattern: %s" % e)
        return EXIT_ERR
    if a.all:
        total = 0
        for k in L.keys():
            _, _, full = L.load(k)
            total += grep_one(k, full, pat, a.C, a.max)
        return 0 if total else 1
    if not a.key:
        log("give a KEY or --all")
        return EXIT_ERR
    key, _, _, full = _load(a.key)
    return 0 if grep_one(key, full, pat, a.C, a.max) else 1


def cmd_quote(a):
    key, meta, document, full = _load(a.key)
    r = Q.check(full, a.text)
    offs = section_offsets(full)
    st = r["status"]
    if a.json:
        r["section"] = section_at(offs, r["start"]) if "start" in r else None
        r["key"] = key
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(st)
        if "start" in r:
            print("section: %s" % section_at(offs, r["start"]))
        if st == Q.VERBATIM:
            print("sentence: %s" % r["context"])
            if r.get("note"):
                print("note: %s" % r["note"])
        elif st == Q.NEAR:
            print("closest passage (similarity %.2f): %s" % (r.get("ratio", 0), r.get("passage")))
            if r.get("note"):
                print("note: %s" % r["note"])
            for d in r.get("diff") or []:
                if d["op"] == "replace":
                    print("  paper says %r, quote says %r" % (d["paper"], d["quote"]))
                elif d["op"] == "delete":
                    print("  quote leaves out %r" % d["paper"])
                elif d["op"] == "insert":
                    print("  quote adds %r" % d["quote"])
            print("sentence: %s" % r["context"])
        else:
            if r.get("closest"):
                print("closest passage (similarity %.2f, too low to count): %s" % (r.get("ratio", 0), r["closest"]))
    return {Q.VERBATIM: 0, Q.NEAR: 1, Q.NOT_FOUND: 2}[st]


# ---------------------------------------------------------------- cite

def _meta_for(k):
    try:
        key = L.find(k)
        return json.load(open(os.path.join(L.path(key), "metadata.json")))
    except L.LitError:
        pass
    kind, i = L.resolve(k)
    if kind == "local":
        raise L.LitError("add the file first: lit add %s" % k)
    m = R.fetch_meta(kind, i) if kind in ("arxiv", "doi", "openreview", "lw") else None
    if not m:
        raise L.LitError("%s did not resolve" % k)
    return m


def canonical_url(m):
    if m.get("arxiv"):
        return "https://arxiv.org/abs/" + m["arxiv"]
    if m.get("doi"):
        return "https://doi.org/" + m["doi"]
    if m.get("openreview"):
        return "https://openreview.net/forum?id=" + m["openreview"]
    return m.get("url") or ""


def bib_name(n):
    parts = n.split()
    if len(parts) < 2:
        return n
    return "%s, %s" % (parts[-1], " ".join(parts[:-1]))


def cmd_cite(a):
    try:
        m = _meta_for(a.id)
    except (L.LitError, net.NetError) as e:
        log(str(e))
        return EXIT_ERR
    authors = [x for x in m.get("authors") or [] if x]
    url = canonical_url(m)
    venue = m.get("venue") or ""
    if venue == "arXiv" and m.get("arxiv"):
        venue = "arXiv:%s" % m["arxiv"]
    if a.style == "bibtex":
        first = re.sub(r"[^a-z]", "", (authors[0].split()[-1] if authors else "anon").lower())
        tw = [w for w in re.findall(r"[a-z]+", (m.get("title") or "").lower()) if w not in ("a", "an", "the", "on", "of")]
        ck = "%s%s%s" % (first, m.get("year") or "", tw[0] if tw else "")
        typ = "misc" if m.get("arxiv") or m.get("lw") or not m.get("venue") else "article"
        fields = [("title", "{%s}" % m.get("title")), ("author", " and ".join(bib_name(x) for x in authors))]
        if m.get("year"):
            fields.append(("year", str(m["year"])))
        if m.get("arxiv"):
            fields += [("eprint", m["arxiv"]), ("archivePrefix", "arXiv")]
            if m.get("category"):
                fields.append(("primaryClass", m["category"]))
        if m.get("venue") and m.get("venue") != "arXiv":
            fields.append(("howpublished" if typ == "misc" else "journal", m["venue"]))
        if m.get("doi"):
            fields.append(("doi", m["doi"]))
        if url:
            fields.append(("url", url))
        print("@%s{%s,\n%s\n}" % (typ, ck, ",\n".join("  %s = {%s}" % (k, v) if not v.startswith("{") else
                                                       "  %s = {%s}" % (k, v) for k, v in fields)))
    else:
        if len(authors) > 3:
            au = ", ".join(authors[:3]) + ", et al."
        else:
            au = ", ".join(authors)
        parts = [au or "(no authors in metadata)", "(%s)." % (m.get("year") or "n.d."), (m.get("title") or "") + "."]
        if venue:
            parts.append(venue + ".")
        if url:
            parts.append(url)
        print(" ".join(parts))
    return 0


# ---------------------------------------------------------------- verify-refs

def cmd_verify_refs(a):
    try:
        with open(a.file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        log(str(e))
        return EXIT_ERR
    entries = R.extract(text)
    if not entries:
        print("no references, URLs, arXiv ids or DOIs found")
        return 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda e: R.check_entry(e, urls=not a.no_urls), entries))
    worst = 0
    rank = {"OK": 0, "UNCHECKED": 1, "MISMATCH": 2, "NOT FOUND": 2}
    rows = []
    for e, (status, detail, meta) in zip(entries, results):
        worst = max(worst, rank[status])
        ident = "%s:%s" % (e["kind"], e["id"]) if e.get("id") else "(text)"
        row = {"line": e["line"], "status": status, "id": ident, "detail": detail,
               "entry": e["text"][:200]}
        if meta:
            row["source_title"] = meta.get("title")
            row["source_first_author"] = (meta.get("authors") or [""])[0]
            row["source_year"] = meta.get("year")
        rows.append(row)
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        for r in rows:
            print("%-9s line %d  %s" % (r["status"], r["line"], r["id"]))
            print("          entry:  %s" % r["entry"][:150])
            if r.get("source_title"):
                print("          source: %s (%s, %s)" % (r["source_title"], r["source_first_author"], r["source_year"]))
            print("          %s" % r["detail"])
        c = {}
        for r in rows:
            c[r["status"]] = c.get(r["status"], 0) + 1
        print("summary: " + ", ".join("%s %d" % kv for kv in sorted(c.items())))
    return worst


# ---------------------------------------------------------------- refs / cites

def _graph_target(k):
    try:
        key = L.find(k)
        m = json.load(open(os.path.join(L.path(key), "metadata.json")))
    except L.LitError:
        kind, i = L.resolve(k)
        m = {"arxiv": i} if kind == "arxiv" else ({"doi": i} if kind == "doi" else {})
        if not m:
            raise L.LitError("refs and cites need an arXiv id or a DOI")
    return m


def cmd_edges(a, kind):
    try:
        m = _graph_target(a.key)
    except L.LitError as e:
        log(str(e))
        return EXIT_ERR
    recs = None
    pid = S.s2_id(m)
    if pid:
        try:
            recs = S.s2_edges(pid, kind, n=1000)
            src = "s2"
        except net.NetError as e:
            log("%s; trying OpenAlex" % e)
    if recs is None:
        doi = m.get("doi") or ("10.48550/arxiv." + m["arxiv"] if m.get("arxiv") else None)
        if not doi:
            log("refs and cites need an arXiv id or a DOI")
            return EXIT_ERR
        try:
            raw = S.oa_raw("doi:" + doi)
            wid = raw["id"].rsplit("/", 1)[-1]
            if kind == "references":
                ids = [w.rsplit("/", 1)[-1] for w in raw.get("referenced_works") or []]
                recs = []
                for i in range(0, len(ids), 50):
                    recs += S.oa_filter("openalex_id:" + "|".join(ids[i:i + 50]), n=50)
                if not ids:
                    log("openalex has no reference list for this work (common for arXiv preprints)")
            else:
                recs = S.oa_filter("cites:" + wid, n=200, sort="cited_by_count:desc")
            src = "openalex"
        except (net.NetError, TypeError, KeyError) as e:
            log("openalex: %s" % e)
            return EXIT_ERR
    if a.sort == "citations":
        recs.sort(key=lambda r: -(r.get("cites") or 0))
    elif a.sort == "year":
        recs.sort(key=lambda r: -(r.get("year") or 0))
    total = len(recs)
    capped = (src == "s2" and total >= 1000) or (src == "openalex" and kind == "citations" and total >= 200)
    recs = recs[: a.n] if a.n else recs
    if a.json:
        for r in recs:
            r["id"] = SR.display_id(r)
        print(json.dumps({"source": src, "total_fetched": total, "capped": capped, "items": recs}, indent=1, ensure_ascii=False))
        return 0
    print("%d %s fetched via %s%s%s" % (total, kind, src,
                                       " (the most this tool fetches; there may be more)" if capped else "",
                                       ", showing %d" % len(recs) if len(recs) < total else ""))
    for i, r in enumerate(recs, 1):
        r["sources"] = [r["source"]]
        print("%3d. %s" % (i, SR.line(r)))
    return 0


# ---------------------------------------------------------------- main

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print("lit: %s" % message, file=sys.stderr)
        sys.exit(64)


def main(argv=None):
    p = Parser(prog="lit", description="Find, fetch, read and check ML and AI-safety papers.")
    p.add_argument("-q", "--quiet", action="store_true", help="no network warnings")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    s = sub.add_parser("search", help="search several sources at once")
    s.add_argument("query")
    s.add_argument("--source", "-s", help="comma list: " + ",".join(S.SEARCHERS) + " (default all but af)")
    s.add_argument("--year", help="2023, 2023-, -2020 or 2019-2022")
    s.add_argument("--n", "-n", type=int, default=10)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("get", help="download full text into the library")
    s.add_argument("ids", nargs="+", metavar="ID_OR_URL")
    s.add_argument("--force", action="store_true", help="fetch again even if present")

    s = sub.add_parser("add", help="add a local PDF, HTML, markdown or text file")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--authors", help="semicolon-separated")
    s.add_argument("--year", type=int)
    s.add_argument("--doi")
    s.add_argument("--arxiv")
    s.add_argument("--key", help="library key to use")

    s = sub.add_parser("list", help="what is in the library")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("toc", help="numbered section tree with word counts")
    s.add_argument("key")

    s = sub.add_parser("read", help="read a section, the abstract, figure captions or tables")
    s.add_argument("key")
    s.add_argument("--section", help='a number ("3.2"), a heading name, or "#N" from toc')
    s.add_argument("--abstract", action="store_true")
    s.add_argument("--figures", action="store_true")
    s.add_argument("--tables", action="store_true")
    s.add_argument("--max-words", type=int, default=0)

    s = sub.add_parser("grep", help="regex search, one sentence per hit with its section")
    s.add_argument("key", nargs="?")
    s.add_argument("pattern")
    s.add_argument("-C", type=int, default=0, help="sentences of context")
    s.add_argument("--all", action="store_true", help="search the whole library")
    s.add_argument("--case", action="store_true", help="case-sensitive")
    s.add_argument("--max", type=int, default=50)

    s = sub.add_parser("quote", help="is this exact text in the paper? exit 0 VERBATIM, 1 NEAR, 2 NOT FOUND")
    s.add_argument("key")
    s.add_argument("text")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("cite", help="a reference built from fetched metadata")
    s.add_argument("id", metavar="KEY_OR_ID")
    s.add_argument("--style", choices=["plain", "bibtex"], default="plain")

    s = sub.add_parser("verify-refs", help="check every reference, id and URL in a file")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-urls", action="store_true",
                   help="do not fetch plain URLs; ids of known paper sources are still checked")

    for name, kind in (("refs", "references"), ("cites", "citations")):
        s = sub.add_parser(name, help="papers this one %s" % ("cites" if name == "refs" else "is cited by"))
        s.add_argument("key", metavar="KEY_OR_ID")
        s.add_argument("--n", "-n", type=int, default=20)
        s.add_argument("--sort", choices=["citations", "year", "none"],
                       default="citations" if name == "cites" else "none")
        s.add_argument("--json", action="store_true")

    a = p.parse_args(argv)
    net.QUIET = a.quiet
    try:
        if a.cmd in ("refs", "cites"):
            return cmd_edges(a, "references" if a.cmd == "refs" else "citations")
        fn = {"search": cmd_search, "get": cmd_get, "add": cmd_add, "list": cmd_list, "toc": cmd_toc,
              "read": cmd_read, "grep": cmd_grep, "quote": cmd_quote, "cite": cmd_cite,
              "verify-refs": cmd_verify_refs}[a.cmd]
        return fn(a)
    except L.LitError as e:
        log(str(e))
        return EXIT_ERR
    except net.NetError as e:
        log(str(e))
        return EXIT_ERR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
