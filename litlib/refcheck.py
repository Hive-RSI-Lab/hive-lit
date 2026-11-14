"""Extract references from a text or markdown file and check each against its source."""
import re

from . import net
from . import sources as S
from .text import STOP, title_key, words

URL_RE = re.compile(r"https?://[^\s<>\"\)\]]+")
REF_HEAD_RE = re.compile(r"^\s*(?:#+\s*|\*\*)?\s*(references|bibliography|works cited|sources|citations|reading list)\b",
                         re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")


def extract(text):
    """Return a list of entries: {line, text, arxiv, doi, openreview, lw, url}."""
    lines = text.split("\n")
    in_refs = False
    entries = []
    cur = None
    for no, raw in enumerate(lines, 1):
        line = raw.strip()
        if REF_HEAD_RE.match(line) and len(line) < 40:
            in_refs = True
            cur = None
            continue
        if re.match(r"^#{1,6}\s", line) and in_refs:
            in_refs = False
        if not line:
            cur = None
            continue
        item = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+|\[\d+\]\s*)", line)
        if cur is not None and in_refs and not item and not re.match(r"^\[[^\]]+\]", line):
            cur["text"] += " " + line
            continue
        cur = {"line": no, "text": line, "in_refs": in_refs}
        entries.append(cur)
    out = []
    for e in entries:
        t = e["text"]
        ids = ids_in(t)
        if ids:
            for d in ids:
                d.update({"line": e["line"], "text": t})
                out.append(d)
        elif e["in_refs"] and len(words(t)) >= 5:
            out.append({"line": e["line"], "text": t, "kind": "text"})
    return out


def ids_in(t):
    found = []
    seen = set()
    for m in S.ARXIV_ID_RE.finditer(t):
        a = m.group(1)
        if ("arxiv", a) not in seen:
            seen.add(("arxiv", a))
            found.append({"kind": "arxiv", "id": a})
    for m in S.DOI_RE.finditer(t):
        d = S.clean_doi(m.group(1))
        a = S.arxiv_from_doi(d)
        if a:
            if ("arxiv", a) not in seen:
                seen.add(("arxiv", a))
                found.append({"kind": "arxiv", "id": a})
        elif ("doi", d) not in seen:
            seen.add(("doi", d))
            found.append({"kind": "doi", "id": d})
    for m in URL_RE.finditer(t):
        u = m.group(0).rstrip(".,;")
        if re.search(r"arxiv\.org|ar5iv|doi\.org", u):
            continue
        mo = re.search(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", u)
        ml = re.search(r"(?:lesswrong\.com|alignmentforum\.org)/(?:posts|s/[^/]+/p)/([A-Za-z0-9]{10,24})", u)
        if mo:
            k = ("openreview", mo.group(1))
        elif ml:
            k = ("lw", ml.group(1))
        else:
            k = ("url", u)
        if k not in seen:
            seen.add(k)
            found.append({"kind": k[0], "id": k[1]})
    return found


def claimed_text(t, ident=""):
    """The entry text without ids and URLs: what the writer says the work is."""
    t = URL_RE.sub(" ", t)
    t = S.ARXIV_ID_RE.sub(" ", t)
    t = S.DOI_RE.sub(" ", t)
    t = re.sub(r"\b(arxiv|doi|preprint|abs|https?)\b", " ", t, flags=re.I)
    return t


def title_match(real_title, claim):
    rt = [w for w in words(real_title) if w not in STOP]
    if not rt:
        return 1.0
    cw = set(words(claim))
    return sum(1 for w in rt if w in cw) / len(rt)


def surname(name):
    parts = [p for p in re.split(r"[\s,]+", name or "") if p]
    if not parts:
        return ""
    return parts[-1]


def fetch_meta(kind, ident):
    if kind == "arxiv":
        return S.arxiv_meta(ident)
    if kind == "doi":
        return S.cr_doi(ident)
    if kind == "openreview":
        return S.orv_note(ident)
    if kind == "lw":
        return S.lw_post(ident)[0]
    return None


def check_entry(e, urls=True):
    """Return (status, detail, meta).

    A plain URL is fetched only when urls is true and its host, and the host
    of every redirect on the way, resolves to public addresses only: a file
    of references must not be able to make lit ask the local machine or the
    local network for anything.
    """
    kind = e["kind"]
    claim = claimed_text(e["text"])
    claim_words = [w for w in words(claim) if w not in STOP and not w.isdigit()]
    if kind == "url":
        if not urls:
            return "UNCHECKED", "skipped: URL checks are off (--no-urls)", None
        try:
            net.public_only(e["id"])
            net.fetch(e["id"], source="url", timeout=20, retries=1, max_bytes=5 * 1024 * 1024,
                      guard=net.public_only)
            return "OK", "URL resolves (title not checked: not a known paper source)", None
        except net.NetError as ex:
            if ex.status == 404 or ex.status == 410:
                return "NOT FOUND", str(ex), None
            return "UNCHECKED", str(ex), None
    if kind == "text":
        return check_text(e["text"])
    try:
        meta = fetch_meta(kind, e["id"])
    except net.NetError as ex:
        if ex.status in (404, 400, 410):
            return "NOT FOUND", "%s %s: %s" % (kind, e["id"], ex), None
        return "UNCHECKED", str(ex), None
    if not meta or not meta.get("title"):
        return "NOT FOUND", "%s %s does not resolve" % (kind, e["id"]), None
    # When the entry is only an id, there is nothing to compare.
    if len(claim_words) < 3:
        return "OK", "resolves (no title or author given to compare)", meta
    tm = title_match(meta["title"], claim)
    authors = meta.get("authors") or []
    fa = surname(authors[0]) if authors else ""
    a_ok = (not fa) or fa.lower() in claim.lower() or _fold(fa) in _fold(claim)
    problems = []
    if tm < 0.7:
        problems.append("title differs (%.0f%% of the real title's words present)" % (tm * 100))
    if not a_ok and len(claim_words) >= 4:
        # Only complain about the author when the line seems to name authors.
        if re.search(r"\b[A-Z][a-z]+(?:,| et al| and | &)", claim):
            problems.append("first author %s not named" % fa)
    if problems:
        return "MISMATCH", "; ".join(problems), meta
    return "OK", "title and first author match", meta


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def check_text(t):
    """A reference with no id: look the title up and see whether a real work fits."""
    q = guess_title(t)
    if len(words(q)) < 3:
        return "UNCHECKED", "could not pick out a title", None
    cands = []
    for fn, name in ((S.oa_search, "openalex"), (S.arxiv_search, "arxiv"), (S.cr_search, "crossref")):
        try:
            cands += fn(q, 5)
        except net.NetError:
            continue
        best = _best(cands, t)
        if best and best[0] >= 0.9:
            break
    best = _best(cands, t)
    if not best:
        return "NOT FOUND", "no work with this title found (searched %s)" % q[:80], None
    tm, m = best
    if tm < 0.85:
        return "NOT FOUND", "closest real title: %s (%s)" % (m["title"][:90], m.get("year")), m
    fa = surname((m.get("authors") or [""])[0])
    if fa and _fold(fa) not in _fold(t) and re.search(r"\b[A-Z][a-z]+(?:,| et al| and | &)", t):
        return "MISMATCH", "title found but first author is %s" % fa, m
    return "OK", "found: %s" % ident_str(m), m


def _best(cands, t):
    best = None
    for m in cands:
        if not m.get("title"):
            continue
        tm = title_match(m["title"], t)
        # also require the found title not to be much shorter than the claim's title
        if best is None or tm > best[0]:
            best = (tm, m)
    return best


def guess_title(t):
    m = re.search(r"[\"“]([^\"”]{8,250})[\"”]", t)
    if m:
        return m.group(1)
    m = re.search(r"\*([^*]{8,250})\*", t)
    if m:
        return m.group(1)
    t2 = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+|\[\d+\]\s*)", "", t)
    # Author list. (Year). Title. Venue.
    m = re.search(r"\(?\b(?:19|20)\d\d[a-z]?\)?[.,:]\s*(.+?)(?:\.\s|$)", t2)
    if m and len(words(m.group(1))) >= 3:
        return m.group(1)
    parts = [p.strip() for p in re.split(r"\.\s+", t2) if p.strip()]
    if len(parts) >= 2:
        return max(parts[:3], key=lambda p: len(words(p)))
    return t2


def ident_str(m):
    if m.get("arxiv"):
        return "arXiv:" + m["arxiv"]
    if m.get("doi"):
        return "doi:" + m["doi"]
    return m.get("url", "")
