"""Parallel search across sources, duplicate merging and ranking."""
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import net
from .sources import SEARCHERS, query_terms
from .text import stem, title_key, words

DEFAULT_SOURCES = ["arxiv", "s2", "openalex", "openreview", "lw", "crossref"]


def run_sources(q, sources, n=10, year=None, timeout=45):
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futs = {ex.submit(SEARCHERS[s], q, n, year): s for s in sources}
        try:
            for f in as_completed(futs, timeout=timeout):
                s = futs[f]
                try:
                    results[s] = f.result()
                except net.NetError as e:
                    errors[s] = str(e)
                except Exception as e:  # a parser fault in one source must not stop the rest
                    errors[s] = "%s: failed (%s: %s)" % (s, type(e).__name__, e)
        except TimeoutError:
            for f, s in futs.items():
                if s not in results and s not in errors:
                    errors[s] = "%s: timed out" % s
    return results, errors


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def rec_keys(r):
    ks = []
    if r.get("arxiv"):
        ks.append("arxiv:" + r["arxiv"].lower())
    if r.get("doi"):
        ks.append("doi:" + r["doi"].lower())
    if r.get("openreview"):
        ks.append("or:" + r["openreview"])
    if r.get("lw"):
        ks.append("lw:" + r["lw"])
    tk = title_key(r.get("title"))
    if len(tk) >= 12:
        ks.append("t:" + tk)
    return ks


PREF = ["arxiv", "s2", "openreview", "openalex", "crossref", "lw", "af"]


def merge(records):
    """Merge records that share an arXiv id, DOI, OpenReview id or normalised title."""
    uf = _UF()
    for i, r in enumerate(records):
        node = "#%d" % i
        uf.find(node)
        for k in rec_keys(r):
            uf.union(node, k)
    groups = {}
    for i, r in enumerate(records):
        groups.setdefault(uf.find("#%d" % i), []).append(r)
    merged = []
    for g in groups.values():
        g.sort(key=lambda r: PREF.index(r["source"]) if r["source"] in PREF else 99)
        m = dict(g[0])
        m["sources"] = []
        m["ranks"] = {}
        for r in g:
            if r["source"] not in m["sources"]:
                m["sources"].append(r["source"])
            m["ranks"][r["source"]] = min(m["ranks"].get(r["source"], 999), r.get("rank", 999))
            for f in ("arxiv", "doi", "openreview", "lw", "s2", "openalex", "pdf_url", "date"):
                if not m.get(f) and r.get(f):
                    m[f] = r[f]
            for f in ("abstract", "venue"):
                if (not m.get(f) or m.get(f) == "arXiv") and r.get(f):
                    m[f] = r[f]
            if r.get("cites") is not None:
                m["cites"] = max(m.get("cites") or 0, r["cites"])
            if not m.get("authors") and r.get("authors"):
                m["authors"] = r["authors"]
            if r.get("year") and (not m.get("year")):
                m["year"] = r["year"]
        # An arXiv record's year is the first version; keep the earliest year seen.
        ys = [r["year"] for r in g if r.get("year")]
        if ys and m.get("arxiv"):
            m["year"] = min(ys)
        if m.get("af") and "af" not in m["sources"]:
            m["sources"].append("af")
        merged.append(m)
    return merged


def score(m, qterms, n_sources):
    qs = {stem(w) for w in qterms}
    tw = {stem(w) for w in words(m.get("title"))}
    aw = {stem(w) for w in words(m.get("abstract"))}
    if qs:
        t_over = len(qs & tw) / len(qs)
        a_over = len(qs & (aw | tw)) / len(qs)
    else:
        t_over = a_over = 0.0
    rrf = sum(1.0 / (3 + rk) for rk in m["ranks"].values())
    cites = m.get("cites") or 0
    s = 1.6 * t_over + 1.0 * a_over + 1.2 * rrf + 0.12 * math.log1p(cites)
    if m.get("score") and m.get("lw"):
        s += 0.12 * math.log1p(max(0, m["score"]))
    # Penalise junk: no title, or very short title.
    if len(m.get("title") or "") < 8:
        s -= 1.0
    m["_score"] = round(s, 4)
    return s


def year_ok(y, spec):
    if not spec or not y:
        return True
    a, _, b = spec.partition("-")
    if "-" not in spec:
        return str(y) == spec
    if a and y < int(a):
        return False
    if b and y > int(b):
        return False
    return True


def search(q, sources=None, n=10, year=None):
    sources = sources or DEFAULT_SOURCES
    t0 = time.monotonic()
    results, errors = run_sources(q, sources, n=n, year=year)
    allrecs = []
    for s, recs in results.items():
        for i, r in enumerate(recs):
            r["rank"] = i
            allrecs.append(r)
    merged = [m for m in merge(allrecs) if year_ok(m.get("year"), year) and m.get("title")]
    qterms = query_terms(q)
    for m in merged:
        score(m, qterms, len(sources))
    merged.sort(key=lambda m: -m["_score"])
    return merged[:n], errors, time.monotonic() - t0


def display_id(m):
    if m.get("arxiv"):
        return "arXiv:" + m["arxiv"]
    if m.get("doi"):
        return "doi:" + m["doi"]
    if m.get("openreview"):
        return "openreview:" + m["openreview"]
    if m.get("lw"):
        return ("af:" if m.get("af") else "lw:") + m["lw"]
    if m.get("s2"):
        return "s2:" + m["s2"]
    return m.get("url") or "?"


def first_author(m):
    a = [x for x in (m.get("authors") or []) if x]
    if not a:
        return "?"
    last = a[0].split()[-1] if a[0].split() else a[0]
    return last + (" et al." if len(a) > 2 else (" & " + a[1].split()[-1] if len(a) == 2 and a[1].split() else ""))


def line(m):
    bits = [display_id(m), str(m.get("year") or "----"), first_author(m), "\"%s\"" % m.get("title", "")]
    v = (m.get("venue") or "").strip()
    if v and v != "arXiv":
        bits.append(v[:40])
    if m.get("cites") is not None:
        bits.append("cites=%d" % m["cites"])
    bits.append("[%s]" % ",".join(m["sources"]))
    return "  ".join(bits)
