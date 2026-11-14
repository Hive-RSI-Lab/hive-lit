"""The local library: ~/.local/share/lit/<key>/{metadata.json,fulltext.txt,sections.json}."""
import json
import os
import re
import shutil
import time
import urllib.parse

from . import doc as D
from . import net
from . import sources as S

LIB = os.path.join(os.path.expanduser("~"), ".local", "share", "lit")


class LitError(Exception):
    pass


def resolve(s):
    """Return (kind, ident) for an id, URL or path."""
    s = s.strip()
    if os.path.exists(s) and not re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", s):
        return "local", os.path.abspath(s)
    low = s.lower()
    m = S.BARE_ARXIV_RE.match(s)
    if m:
        return "arxiv", m.group(1)
    m = S.ARXIV_ID_RE.search(s)
    if m and ("arxiv" in low or "ar5iv" in low):
        return "arxiv", m.group(1)
    if "openreview.net" in low or low.startswith("openreview:"):
        q = urllib.parse.urlsplit(s)
        ident = urllib.parse.parse_qs(q.query).get("id", [None])[0]
        if low.startswith("openreview:"):
            ident = s.split(":", 1)[1]
        if ident:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
                raise LitError("openreview: %r is not a valid note id (letters, digits, '-' and '_' only)" % ident)
            return "openreview", ident
    m = re.search(r"(?:lesswrong\.com|alignmentforum\.org)/(?:posts|s/[^/]+/p)/([A-Za-z0-9]{10,24})", s)
    if m:
        return "lw", m.group(1)
    m = re.match(r"^(?:lw|af):([A-Za-z0-9]{10,24})$", s)
    if m:
        return "lw", m.group(1)
    m = S.DOI_RE.search(s)
    if m and ("doi" in low or s.startswith("10.")):
        d = S.clean_doi(m.group(1))
        a = S.arxiv_from_doi(d)
        return ("arxiv", a) if a else ("doi", d)
    m = re.match(r"^s2:([0-9a-f]{40})$", s)
    if m:
        return "s2", m.group(1)
    raise LitError("cannot tell what %r is: give an arXiv id or URL, a DOI, an OpenReview id or URL, "
                   "a LessWrong or Alignment Forum post URL, or a local file path" % s)


KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _part(s):
    """An id as part of a key: letters, digits, '.', '_' and '-' stay, anything else becomes '_'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s))[:80]


def key_for(kind, ident, title=""):
    if kind == "arxiv":
        return "arxiv-" + _part(ident)
    if kind == "doi":
        return "doi-" + re.sub(r"[^a-z0-9._-]+", "_", ident.lower())[:80]
    if kind == "openreview":
        return "openreview-" + _part(ident)
    if kind == "lw":
        return "lw-" + _part(ident)
    slug = re.sub(r"[^a-z0-9]+", "-", (title or os.path.basename(ident)).lower()).strip("-")[:50]
    return "local-" + (slug or "doc")


def path(key):
    """The folder of one paper. A key is a single folder name; anything else is refused, so no key
    (typed, or built from an id a web page returned) can point outside the library."""
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise LitError("%r is not a library key (letters, digits, '.', '_' and '-' only)" % (key,))
    return os.path.join(LIB, key)


def keys():
    if not os.path.isdir(LIB):
        return []
    return sorted(k for k in os.listdir(LIB) if os.path.isfile(os.path.join(LIB, k, "metadata.json")))


def find(key_or_id):
    """Find a library key from a key, id, URL or unique prefix."""
    ks = keys()
    if key_or_id in ks:
        return key_or_id
    try:
        kind, ident = resolve(key_or_id)
        if kind != "local":
            k = key_for(kind, ident)
            if k in ks:
                return k
    except LitError:
        pass
    hits = [k for k in ks if k.startswith(key_or_id) or k.split("-", 1)[-1].startswith(key_or_id)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise LitError("%r matches several papers: %s" % (key_or_id, ", ".join(hits[:8])))
    raise LitError("%r is not in the library; run: lit get %s" % (key_or_id, key_or_id))


def load(key):
    p = path(key)
    with open(os.path.join(p, "metadata.json")) as f:
        meta = json.load(f)
    with open(os.path.join(p, "sections.json")) as f:
        secs = json.load(f)
    with open(os.path.join(p, "fulltext.txt")) as f:
        full = f.read()
    return meta, secs, full


def save(key, meta, document, raw=None, raw_name=None):
    p = path(key)
    os.makedirs(p, exist_ok=True)
    meta = dict(meta)
    meta["key"] = key
    meta["fetched"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta["n_sections"] = len(document["sections"])
    meta["n_words"] = sum(len(s["text"].split()) for s in document["sections"])
    for k in ("rank", "source", "_score", "ranks"):
        meta.pop(k, None)
    with open(os.path.join(p, "metadata.json"), "w") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)
    with open(os.path.join(p, "sections.json"), "w") as f:
        json.dump(document, f, indent=1, ensure_ascii=False)
    with open(os.path.join(p, "fulltext.txt"), "w") as f:
        f.write(D.fulltext(document))
    if raw is not None and raw_name:
        with open(os.path.join(p, raw_name), "wb") as f:
            f.write(raw)
    return meta


def _good(document, min_sections=3):
    words = sum(len(s["text"].split()) for s in document["sections"])
    return len(document["sections"]) >= min_sections and words > 800


def _pdf_doc(data, tmpname):
    os.makedirs(net.CACHE_DIR, exist_ok=True)
    tmp = os.path.join(net.CACHE_DIR, tmpname)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        return D.from_pdf(tmp)
    finally:
        os.unlink(tmp)


def _is_pdf(data):
    return data[:5] == b"%PDF-"


def get_arxiv(aid, log):
    meta = S.arxiv_meta(aid)
    if not meta:
        raise LitError("arxiv: no paper with id %s" % aid)
    vid = aid + ("v" + meta["version"] if meta.get("version") and "v" not in aid else "")
    tried = []
    for name, url in (("arxiv-html", "https://arxiv.org/html/" + vid),
                      ("ar5iv", "https://ar5iv.labs.arxiv.org/html/" + aid)):
        try:
            body = net.fetch(url, source=name, timeout=60, retries=1)
            html = body.decode("utf-8", "replace")
            if "ltx_document" not in html and "ltx_page_main" not in html:
                tried.append(name + ": no HTML version")
                continue
            document = D.from_html(html)
            if _good(document):
                meta["fulltext_source"] = url
                return meta, document, body, "source.html"
            tried.append(name + ": HTML too thin")
        except net.NetError as e:
            tried.append(str(e))
    for t in tried:
        log(t + "; trying next")
    url = "https://arxiv.org/pdf/" + vid
    body = net.fetch(url, source="arxiv-pdf", timeout=90, retries=2)
    if not _is_pdf(body):
        raise LitError("arxiv-pdf: %s did not return a PDF" % url)
    document = _pdf_doc(body, "a-%s.pdf" % aid.replace("/", "_"))
    meta["fulltext_source"] = url + " (pdftotext -layout)"
    return meta, document, body, "source.pdf"


ORV_BLOCKED = ("openreview: the site answered with a bot check (HTTP 403) and lit cannot pass it; "
               "if the paper is also on arXiv, find it with: lit search \"TITLE\" -s arxiv, then lit get ARXIV_ID; "
               "or download the PDF in a browser and run: lit add FILE.pdf")


def get_openreview(nid, log):
    try:
        meta = S.orv_note(nid)
    except net.NetError as e:
        if e.status == 403:
            raise LitError(ORV_BLOCKED)
        raise
    if not meta:
        raise LitError("openreview: no note with id %s" % nid)
    url = "https://openreview.net/pdf?id=" + nid
    try:
        body = net.fetch(url, source="openreview-pdf", timeout=90, retries=2)
    except net.NetError as e:
        if e.status == 403:
            raise LitError(ORV_BLOCKED)
        raise
    if not _is_pdf(body):
        raise LitError("openreview-pdf: no PDF for %s" % nid)
    document = _pdf_doc(body, "o-%s.pdf" % nid)
    meta["fulltext_source"] = url + " (pdftotext -layout)"
    return meta, document, body, "source.pdf"


def get_lw(pid, log):
    meta, html = S.lw_post(pid)
    if not meta:
        raise LitError("lesswrong: no post with id %s" % pid)
    document = D.from_html("<article>" + html + "</article>")
    meta["fulltext_source"] = meta["url"] + " (GraphQL contents.html)"
    return meta, document, html.encode("utf-8"), "source.html"


def get_doi(doi, log):
    meta = None
    try:
        meta = S.cr_doi(doi)
    except net.NetError as e:
        log(str(e))
    oa = None
    try:
        oa = S.oa_work("doi:" + doi)
    except net.NetError as e:
        log(str(e))
    if oa and oa.get("arxiv"):
        log("DOI %s has an arXiv version %s; fetching that" % (doi, oa["arxiv"]))
        m, d, raw, rn = get_arxiv(oa["arxiv"], log)
        m["doi"] = doi
        return m, d, raw, rn
    meta = meta or oa
    if not meta or not meta.get("title"):
        raise LitError("doi: %s did not resolve at Crossref or OpenAlex" % doi)
    pdf = (oa or {}).get("pdf_url")
    if pdf:
        try:
            body = net.fetch(pdf, source="open-access PDF", timeout=90, retries=1)
            if _is_pdf(body):
                document = _pdf_doc(body, "d-%s.pdf" % re.sub(r"\W", "_", doi)[:60])
                meta["fulltext_source"] = pdf + " (pdftotext -layout)"
                return meta, document, body, "source.pdf"
        except net.NetError as e:
            log(str(e))
    raise LitError("doi: found the metadata for %s (%s) but no open full text; download the PDF "
                   "yourself and run: lit add FILE.pdf --doi %s" % (doi, meta["title"][:60], doi))


def get_local(p, title=None, log=print):
    with open(p, "rb") as f:
        data = f.read()
    if _is_pdf(data):
        document = D.from_pdf(p)
        src = "pdftotext -layout"
    else:
        txt = data.decode("utf-8", "replace")
        if re.search(r"<html|<body|<article|<p>", txt[:5000], re.I):
            document = D.from_html(txt)
            src = "html"
        else:
            document = D.from_plain(txt)
            src = "text"
    t = title or document.get("title") or guess_title(document) or os.path.basename(p)
    meta = S.blank("local")
    # Only the file's base name is stored: the folder it came from stays out of the library.
    name = os.path.basename(p)
    meta.update({"title": t, "path": name, "url": "", "fulltext_source": name + " (" + src + ")"})
    return meta, document


def guess_title(document):
    if not document["sections"]:
        return ""
    fm = document["sections"][0]
    if fm["title"] != "Front matter" or not fm["text"]:
        return ""
    first = fm["text"].split("\n\n")[0].split("\n")
    t = " ".join(x.strip() for x in first[:2] if x.strip())
    t = D.fix_smallcaps(t)
    t = re.sub(r"(\w) ?- (\w)", lambda m: m.group(1) + m.group(2).lower(), t)
    return t[:200]


def get(ident, log, force=False):
    kind, i = resolve(ident)
    if kind == "local":
        raise LitError("for a local file use: lit add %s" % ident)
    if kind == "s2":
        raise LitError("give an arXiv id or DOI rather than a Semantic Scholar id")
    key = key_for(kind, i)
    if not force and os.path.exists(os.path.join(path(key), "metadata.json")):
        return key, json.load(open(os.path.join(path(key), "metadata.json"))), True
    fn = {"arxiv": get_arxiv, "openreview": get_openreview, "lw": get_lw, "doi": get_doi}[kind]
    meta, document, raw, raw_name = fn(i, log)
    if kind == "doi" and meta.get("arxiv"):
        key = key_for("arxiv", meta["arxiv"])
    meta = save(key, meta, document, raw, raw_name)
    return key, meta, False


def remove(key):
    shutil.rmtree(path(key))
