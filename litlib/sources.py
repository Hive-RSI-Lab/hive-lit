"""Search and metadata adapters for each source.

Every adapter returns a list of plain records:
  {title, authors, year, venue, abstract, arxiv, doi, openreview, lw, url,
   cites, source}
"""
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import net
from .text import STOP, words

ATOM = "{http://www.w3.org/2005/Atom}"
ARXNS = "{http://arxiv.org/schemas/atom}"

ARXIV_ID_RE = re.compile(
    r"(?:arxiv[:\s/]*|arxiv\.org/(?:abs|pdf|html)/|ar5iv(?:\.labs\.arxiv)?\.org/(?:abs|html)/)"
    r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
BARE_ARXIV_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\)]+)", re.I)


def clean_doi(d):
    d = d.strip().rstrip(".,;")
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    return d.lower()


def arxiv_from_doi(d):
    m = re.match(r"10\.48550/arxiv\.(.+)$", d or "", re.I)
    return m.group(1) if m else None


def blank(source):
    return {"title": "", "authors": [], "year": None, "venue": "", "abstract": "",
            "arxiv": None, "doi": None, "openreview": None, "lw": None,
            "url": "", "cites": None, "source": source}


def query_terms(q):
    return [w for w in words(q) if w not in STOP]


# ---------------------------------------------------------------- arXiv

def parse_arxiv_atom(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall(ATOM + "entry"):
        r = blank("arxiv")
        idurl = (e.findtext(ATOM + "id") or "").strip()
        m = ARXIV_ID_RE.search(idurl)
        if not m:
            continue
        r["arxiv"] = m.group(1)
        r["version"] = (m.group(2) or "").lstrip("v") or None
        r["title"] = " ".join((e.findtext(ATOM + "title") or "").split())
        r["abstract"] = " ".join((e.findtext(ATOM + "summary") or "").split())
        r["authors"] = [" ".join((a.findtext(ATOM + "name") or "").split())
                        for a in e.findall(ATOM + "author")]
        pub = e.findtext(ATOM + "published") or ""
        r["date"] = pub[:10]
        r["year"] = int(pub[:4]) if pub[:4].isdigit() else None
        doi = e.findtext(ARXNS + "doi")
        if doi:
            r["doi"] = clean_doi(doi)
        jr = e.findtext(ARXNS + "journal_ref")
        r["venue"] = " ".join(jr.split()) if jr else "arXiv"
        pc = e.find(ARXNS + "primary_category")
        if pc is not None:
            r["category"] = pc.get("term")
        r["url"] = "https://arxiv.org/abs/" + r["arxiv"]
        out.append(r)
    return out


def arxiv_search(q, n=10, year=None):
    terms = query_terms(q)[:8]
    if not terms:
        return []
    # Title and abstract fields; AND first, then OR if too few.
    def run(joiner):
        sq = joiner.join("(ti:%s OR abs:%s)" % (t, t) for t in terms)
        url = "https://export.arxiv.org/api/query?" + net.qs(
            {"search_query": sq, "start": 0, "max_results": max(n, 10),
             "sortBy": "relevance"})
        return parse_arxiv_atom(net.fetch(url, source="arxiv", timeout=30, retries=2))
    res = run(" AND ")
    if len(res) < n and len(terms) > 2:
        seen = {r["arxiv"] for r in res}
        res += [r for r in run(" OR ") if r["arxiv"] not in seen]
    return res


def arxiv_meta(aid):
    url = "https://export.arxiv.org/api/query?" + net.qs({"id_list": aid, "max_results": 1})
    res = parse_arxiv_atom(net.fetch(url, source="arxiv", timeout=30))
    res = [r for r in res if r["title"] and r["title"].lower() != "error"]
    return res[0] if res else None


# ---------------------------------------------------------------- Semantic Scholar

S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,year,authors,externalIds,venue,citationCount,abstract,publicationDate"


def s2_record(p):
    r = blank("s2")
    if not p:
        return r
    r["title"] = p.get("title") or ""
    r["year"] = p.get("year")
    r["authors"] = [a.get("name", "") for a in (p.get("authors") or [])]
    r["venue"] = p.get("venue") or ""
    r["cites"] = p.get("citationCount")
    r["abstract"] = p.get("abstract") or ""
    ext = p.get("externalIds") or {}
    if ext.get("ArXiv"):
        r["arxiv"] = ext["ArXiv"]
    if ext.get("DOI"):
        d = clean_doi(ext["DOI"])
        r["doi"] = d
        r["arxiv"] = r["arxiv"] or arxiv_from_doi(d)
    r["s2"] = p.get("paperId")
    r["url"] = ("https://arxiv.org/abs/" + r["arxiv"]) if r["arxiv"] else (
        "https://doi.org/" + r["doi"] if r["doi"] else
        "https://www.semanticscholar.org/paper/" + (p.get("paperId") or ""))
    return r


def s2_search(q, n=10, year=None):
    params = {"query": q, "limit": max(n, 10), "fields": S2_FIELDS}
    if year:
        params["year"] = year
    d = net.get_json(S2 + "/paper/search?" + net.qs(params), source="s2",
                     retries=2, give_up_on_429=True)
    return [s2_record(p) for p in d.get("data") or []]


def s2_id(rec_or_id):
    """Semantic Scholar paper id form for a record or an id string."""
    if isinstance(rec_or_id, dict):
        if rec_or_id.get("arxiv"):
            return "arXiv:" + rec_or_id["arxiv"]
        if rec_or_id.get("doi"):
            return "DOI:" + rec_or_id["doi"]
        return None
    return rec_or_id


def s2_paper(pid):
    return s2_record(net.get_json(S2 + "/paper/" + urllib.parse.quote(pid, safe=":") +
                                  "?fields=" + S2_FIELDS, source="s2", retries=2,
                                  give_up_on_429=True))


def s2_edges(pid, kind, n=20):
    """kind is 'references' or 'citations'."""
    fields = "title,year,authors,externalIds,venue,citationCount"
    url = "%s/paper/%s/%s?%s" % (S2, urllib.parse.quote(pid, safe=":"), kind,
                                 net.qs({"fields": fields, "limit": min(max(n, 1), 1000)}))
    d = net.get_json(url, source="s2", retries=2, give_up_on_429=True)
    key = "citedPaper" if kind == "references" else "citingPaper"
    return [s2_record(x.get(key)) for x in d.get("data") or [] if x.get(key)]


# ---------------------------------------------------------------- OpenAlex

OA = "https://api.openalex.org"
OA_SELECT = ("id,doi,title,publication_year,cited_by_count,ids,authorships,"
             "primary_location,abstract_inverted_index,best_oa_location,locations")


def oa_abstract(inv):
    if not inv:
        return ""
    pos = []
    for w, idx in inv.items():
        for i in idx:
            pos.append((i, w))
    pos.sort()
    return " ".join(w for _, w in pos)


def oa_record(w):
    r = blank("openalex")
    r["title"] = w.get("title") or ""
    r["year"] = w.get("publication_year")
    r["cites"] = w.get("cited_by_count")
    r["authors"] = [((a.get("author") or {}).get("display_name") or "")
                    for a in (w.get("authorships") or [])]
    r["openalex"] = (w.get("id") or "").rsplit("/", 1)[-1]
    if w.get("doi"):
        r["doi"] = clean_doi(w["doi"])
        r["arxiv"] = arxiv_from_doi(r["doi"])
    for loc in (w.get("locations") or []) + [w.get("primary_location") or {}]:
        u = (loc or {}).get("landing_page_url") or ""
        m = ARXIV_ID_RE.search(u)
        if m and not r["arxiv"]:
            r["arxiv"] = m.group(1)
    src = ((w.get("primary_location") or {}).get("source") or {})
    r["venue"] = src.get("display_name") or ""
    r["abstract"] = oa_abstract(w.get("abstract_inverted_index"))
    oa = w.get("best_oa_location") or {}
    r["pdf_url"] = oa.get("pdf_url")
    r["url"] = ("https://arxiv.org/abs/" + r["arxiv"]) if r["arxiv"] else (
        "https://doi.org/" + r["doi"] if r["doi"] else (w.get("id") or ""))
    return r


def oa_search(q, n=10, year=None):
    params = {"search": q, "per-page": max(n, 10), "select": OA_SELECT}
    if year:
        a, _, b = year.partition("-")
        f = []
        if a:
            f.append("from_publication_date:%s-01-01" % a)
        if b:
            f.append("to_publication_date:%s-12-31" % b)
        if "-" not in year:
            f = ["publication_year:%s" % year]
        params["filter"] = ",".join(f)
    d = net.get_json(OA + "/works?" + net.qs(params), source="openalex", retries=2)
    return [oa_record(w) for w in d.get("results") or []]


def oa_work(ident):
    """ident: 'doi:10.x/..' or a W id."""
    if ident.startswith("doi:"):
        url = OA + "/works/https://doi.org/" + ident[4:]
    else:
        url = OA + "/works/" + ident
    return oa_record(net.get_json(url + "?" + net.qs({"select": OA_SELECT + ",referenced_works"}),
                                  source="openalex", retries=2))


def oa_raw(ident):
    if ident.startswith("doi:"):
        url = OA + "/works/https://doi.org/" + ident[4:]
    else:
        url = OA + "/works/" + ident
    return net.get_json(url + "?select=id,referenced_works,cited_by_count",
                        source="openalex", retries=2)


def oa_filter(filt, n=20, sort=None):
    params = {"filter": filt, "per-page": min(max(n, 1), 200), "select": OA_SELECT}
    if sort:
        params["sort"] = sort
    d = net.get_json(OA + "/works?" + net.qs(params), source="openalex", retries=2)
    return [oa_record(w) for w in d.get("results") or []]


# ---------------------------------------------------------------- Crossref

CR = "https://api.crossref.org/works"


def cr_record(it):
    r = blank("crossref")
    r["title"] = " ".join((it.get("title") or [""])[0].split())
    r["doi"] = clean_doi(it.get("DOI") or "") or None
    r["arxiv"] = arxiv_from_doi(r["doi"])
    r["authors"] = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
                    for a in it.get("author") or []]
    dp = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
    r["year"] = dp[0] if dp and isinstance(dp[0], int) else None
    r["venue"] = (it.get("container-title") or [""])[0] if it.get("container-title") else ""
    r["cites"] = it.get("is-referenced-by-count")
    r["abstract"] = re.sub(r"<[^>]+>", " ", it.get("abstract") or "")
    r["url"] = "https://doi.org/" + r["doi"] if r["doi"] else ""
    return r


def cr_search(q, n=10, year=None):
    params = {"query.bibliographic": q, "rows": max(n, 10),
              "select": "DOI,title,author,issued,container-title,is-referenced-by-count,abstract"}
    if year:
        a, _, b = year.partition("-")
        f = []
        if "-" not in year:
            a = b = year
        if a:
            f.append("from-pub-date:%s" % a)
        if b:
            f.append("until-pub-date:%s" % b)
        params["filter"] = ",".join(f)
    d = net.get_json(CR + "?" + net.qs(params), source="crossref", retries=2)
    return [cr_record(it) for it in (d.get("message") or {}).get("items") or []]


def cr_doi(doi):
    d = net.get_json(CR + "/" + urllib.parse.quote(doi, safe="/"), source="crossref", retries=2)
    return cr_record(d.get("message") or {})


# ---------------------------------------------------------------- OpenReview

ORV = "https://api2.openreview.net"


def _orv(c, k):
    v = c.get(k)
    if isinstance(v, dict):
        v = v.get("value")
    return v


def orv_record(nt):
    import datetime
    r = blank("openreview")
    c = nt.get("content") or {}
    r["title"] = " ".join((_orv(c, "title") or "").split())
    r["authors"] = list(_orv(c, "authors") or [])
    r["abstract"] = " ".join((_orv(c, "abstract") or "").split())
    r["venue"] = _orv(c, "venue") or ""
    ts = nt.get("pdate") or nt.get("odate") or nt.get("cdate") or nt.get("tcdate")
    if ts:
        r["year"] = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).year
    m = re.search(r"\b(20\d\d)\b", r["venue"])
    if m:
        r["year"] = int(m.group(1))
    r["openreview"] = nt.get("forum") or nt.get("id")
    r["url"] = "https://openreview.net/forum?id=" + r["openreview"]
    pdf = _orv(c, "pdf")
    r["pdf_url"] = ("https://openreview.net" + pdf) if pdf and pdf.startswith("/") else (
        "https://openreview.net/pdf?id=" + r["openreview"])
    return r


def orv_search(q, n=10, year=None):
    url = ORV + "/notes/search?" + net.qs({"term": q, "limit": max(n, 10), "source": "forum",
                                           "type": "terms", "content": "all"})
    d = net.get_json(url, source="openreview", retries=2)
    out = []
    for nt in d.get("notes") or []:
        r = orv_record(nt)
        if r["title"] and (nt.get("id") == nt.get("forum")):
            v = r["venue"].lower()
            if "withdrawn" in v or "desk rejected" in v:
                r["venue"] = r["venue"] or "withdrawn"
            out.append(r)
    return out


def orv_note(nid):
    d = net.get_json(ORV + "/notes?" + net.qs({"id": nid}), source="openreview", retries=2)
    notes = d.get("notes") or []
    if not notes:
        # Older API v1 venues.
        d = net.get_json("https://api.openreview.net/notes?" + net.qs({"id": nid}),
                         source="openreview", retries=2)
        notes = d.get("notes") or []
    return orv_record(notes[0]) if notes else None


# ---------------------------------------------------------------- LessWrong / Alignment Forum

LW = "https://www.lesswrong.com"


def lw_record(h, source="lw"):
    import datetime
    r = blank(source)
    r["title"] = h.get("title") or ""
    r["lw"] = h.get("objectID") or h.get("_id")
    r["authors"] = [h.get("authorDisplayName") or ((h.get("user") or {}).get("displayName")) or ""]
    when = h.get("postedAt") or ""
    r["date"] = when[:10]
    r["year"] = int(when[:4]) if when[:4].isdigit() else None
    r["af"] = bool(h.get("af"))
    r["venue"] = "Alignment Forum" if r["af"] else "LessWrong"
    r["cites"] = None
    r["score"] = h.get("baseScore")
    r["abstract"] = " ".join((h.get("body") or "")[:1500].split())
    slug = h.get("slug") or ""
    base = "https://www.alignmentforum.org" if r["af"] else LW
    r["url"] = "%s/posts/%s/%s" % (base, r["lw"], slug)
    return r


def lw_search(q, n=10, year=None, af_only=False):
    body = [{"indexName": "test_posts",
             "params": {"query": q, "hitsPerPage": max(n, 10) * (2 if af_only else 1)}}]
    d = net.get_json(LW + "/api/search", data=body, source="lesswrong", retries=2)
    hits = (d[0] if isinstance(d, list) and d else {}).get("hits") or []
    out = []
    for h in hits:
        r = lw_record(h)
        if af_only and not r["af"]:
            continue
        out.append(r)
    return out


def lw_post(pid):
    query = ("query($id:String){ post(input:{selector:{_id:$id}}) { result { _id title slug "
             "postedAt af baseScore user { displayName } coauthors { displayName } "
             "contents { html } } } }")
    d = net.get_json(LW + "/graphql", data={"query": query, "variables": {"id": pid}},
                     source="lesswrong", retries=2)
    p = (((d.get("data") or {}).get("post") or {}).get("result"))
    if not p:
        return None, ""
    r = lw_record(p)
    r["authors"] += [c.get("displayName", "") for c in p.get("coauthors") or []]
    return r, ((p.get("contents") or {}).get("html") or "")


SEARCHERS = {
    "arxiv": arxiv_search,
    "s2": s2_search,
    "openalex": oa_search,
    "openreview": orv_search,
    "lw": lambda q, n=10, year=None: lw_search(q, n, year),
    "af": lambda q, n=10, year=None: lw_search(q, n, year, af_only=True),
    "crossref": cr_search,
}
