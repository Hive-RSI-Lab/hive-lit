"""Turn HTML, pdftotext output or plain text into a list of sections.

A document is a dict:
  {"sections": [{"num", "title", "level", "text"}], "figures": [...], "tables": [...]}
fulltext.txt is the sections joined with their headings.
"""
import re
import subprocess
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "nav", "header", "footer", "button", "noscript", "svg",
             "annotation", "annotation-xml", "head", "title", "form", "select", "template"}
SKIP_CLASSES = ("ltx_page_header", "ltx_page_footer", "package-alerts", "ltx_authors",
                "ltx_dates", "ltx_role_affiliation", "ar5iv-footer", "ltx_page_logo",
                "html-header", "ltx_rdf", "sr-only", "visually-hidden", "ltx_ERROR")
BLOCK = {"p", "div", "li", "ul", "ol", "section", "article", "blockquote", "br", "dd", "dt",
         "dl", "pre", "figure", "figcaption", "table", "tr", "hr", "h1", "h2", "h3", "h4",
         "h5", "h6", "caption", "tbody", "thead", "main", "aside"}
VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "source", "wbr"}

NUM_RE = re.compile(r"^\s*(?:(?:Appendix|APPENDIX)\s+)?((?:[A-Z]|\d+|[IVX]+)(?:\.\d+)*)\.?\s+(\S.*)$")


def split_num(title):
    t = " ".join(title.split())
    m = NUM_RE.match(t)
    if m and len(m.group(1)) <= 8:
        num = m.group(1)
        # Single capital letters only count as numbers for appendices like "A Proofs".
        if re.fullmatch(r"[A-Z]", num) and not re.match(r"^\s*(Appendix\s+)?[A-Z]\s+[A-Z0-9]", t):
            return "", t
        if re.fullmatch(r"[IVX]+", num) and not t.startswith(num + " ") and not t.startswith(num + "."):
            return "", t
        return num, m.group(2).strip()
    return "", t


class _HTMLDoc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []          # (tag, skipping)
        self.skip = 0
        self.blocks = []         # ("h", level, text) | ("p", text) | ("fig", text) | ("table", caption, text)
        self.buf = []
        self.in_heading = None
        self.hbuf = []
        self.fig_depth = []      # stack of {"kind", "caption", "rows"}
        self.in_caption = 0
        self.cbuf = []
        self.row = None
        self.title = ""
        self.in_title_doc = False

    # -- helpers
    def flush(self):
        t = " ".join("".join(self.buf).split())
        self.buf = []
        if t:
            if self.fig_depth and self.fig_depth[-1]["kind"] == "table" and not self.in_caption:
                self.fig_depth[-1]["rows"].append(t)
            else:
                self.blocks.append(("p", t))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in VOID:
            if tag == "br":
                self.buf.append("\n")
            if tag == "img" and self.skip == 0 and "ltx_Math" in cls.split() and a.get("alt"):
                self.buf.append(a["alt"])
            return
        toks = cls.split()
        skipping = (tag in SKIP_TAGS or any(c in toks for c in SKIP_CLASSES)
                    or a.get("aria-hidden") == "true" and tag != "span")
        if tag == "math":
            alt = a.get("alttext")
            if alt and self.skip == 0:
                self._text(alt)
            skipping = True
        self.stack.append((tag, skipping))
        if skipping:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in BLOCK and tag not in ("tr",):
            if self.in_heading is None and not self.in_caption:
                self.flush()
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.in_heading = int(tag[1])
            self.hbuf = []
            if "ltx_title_document" in cls:
                self.in_title_doc = True
        elif tag == "figure":
            kind = "table" if "ltx_table" in cls.split() else "figure"
            self.fig_depth.append({"kind": kind, "caption": "", "rows": []})
        elif tag == "table" and not self.fig_depth:
            self.fig_depth.append({"kind": "table", "caption": "", "rows": [], "bare": True})
        elif tag in ("figcaption", "caption"):
            self.in_caption += 1
            self.cbuf = []
        elif tag == "tr":
            self.flush()
            self.row = []
        elif tag in ("td", "th"):
            if self.row is not None:
                self.flush()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # pop to the matching tag
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                break
        else:
            return
        popped = self.stack[k:]
        del self.stack[k:]
        for t, sk in popped:
            if sk:
                self.skip -= 1
        if self.skip:
            return
        if re.fullmatch(r"h[1-6]", tag) and self.in_heading is not None:
            text = " ".join("".join(self.hbuf).split())
            if self.in_title_doc:
                self.title = text
                self.in_title_doc = False
            elif text:
                self.blocks.append(("h", self.in_heading, text))
            self.in_heading = None
            return
        if tag in ("figcaption", "caption") and self.in_caption:
            self.in_caption -= 1
            cap = " ".join("".join(self.cbuf).split())
            if self.fig_depth:
                self.fig_depth[-1]["caption"] = cap
            elif cap:
                self.blocks.append(("p", cap))
            return
        if tag in ("td", "th") and self.row is not None:
            cell = " ".join("".join(self.buf).split())
            self.buf = []
            self.row.append(cell)
            return
        if tag == "tr" and self.row is not None:
            self.flush()
            line = " | ".join(c for c in self.row if c)
            if line and self.fig_depth:
                self.fig_depth[-1]["rows"].append(line)
            elif line:
                self.blocks.append(("p", line))
            self.row = None
            return
        if (tag == "figure" or (tag == "table" and self.fig_depth and self.fig_depth[-1].get("bare"))) and self.fig_depth:
            self.flush()
            f = self.fig_depth.pop()
            if f["kind"] == "table":
                self.blocks.append(("table", f["caption"], "\n".join(f["rows"])))
            else:
                if f["rows"]:
                    for r in f["rows"]:
                        self.blocks.append(("p", r))
                self.blocks.append(("fig", f["caption"]))
            return
        if tag in BLOCK and self.in_heading is None and not self.in_caption:
            self.flush()

    def _text(self, data):
        if self.in_heading is not None:
            self.hbuf.append(data)
        elif self.in_caption:
            self.cbuf.append(data)
        else:
            self.buf.append(data)

    def handle_data(self, data):
        if self.skip:
            return
        self._text(data)

    def close(self):
        super().close()
        self.flush()


def from_html(html):
    p = _HTMLDoc()
    p.feed(html)
    p.close()
    return blocks_to_doc(p.blocks, p.title)


def blocks_to_doc(blocks, title=""):
    levels = sorted({b[1] for b in blocks if b[0] == "h" and b[1] < 6})
    rank = {lv: i + 1 for i, lv in enumerate(levels)}
    sections = []
    figures, tables = [], []
    cur = {"num": "", "title": "Front matter", "level": 1, "paras": []}
    sections.append(cur)
    for b in blocks:
        if b[0] == "h":
            num, t = split_num(b[2])
            lvl = rank.get(b[1], 1) if b[1] < 6 else 1
            if num:
                lvl = num.count(".") + 1
            cur = {"num": num, "title": t, "level": lvl, "paras": []}
            sections.append(cur)
        elif b[0] == "p":
            cur["paras"].append(b[1])
        elif b[0] == "fig":
            if b[1]:
                figures.append({"caption": b[1], "section": label(cur)})
                cur["paras"].append(b[1])
        elif b[0] == "table":
            if not b[1]:
                if b[2]:
                    cur["paras"].append(b[2])
                continue
            tables.append({"caption": b[1], "text": b[2], "section": label(cur)})
            if b[1]:
                cur["paras"].append(b[1])
            if b[2]:
                cur["paras"].append(b[2])
    return finish(sections, figures, tables, title)


def label(s):
    return (s["num"] + " " + s["title"]).strip()


def finish(sections, figures, tables, title=""):
    out = []
    for s in sections:
        text = "\n\n".join(s["paras"]).strip()
        if not text and s["title"] == "Front matter":
            continue
        out.append({"num": s["num"], "title": s["title"], "level": s["level"], "text": text})
    # Abstract: a section titled Abstract, else the first paragraph of front matter.
    return {"title": title, "sections": out, "figures": figures, "tables": tables}


def fulltext(doc):
    parts = []
    for s in doc["sections"]:
        parts.append(("#" * max(1, s["level"])) + " " + label(s))
        if s["text"]:
            parts.append(s["text"])
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------- PDF


def pdftotext(path, layout=True):
    args = ["pdftotext", "-enc", "UTF-8"]
    if layout:
        args.append("-layout")
    args += [path, "-"]
    r = subprocess.run(args, capture_output=True, timeout=180)
    if r.returncode != 0 and not r.stdout:
        raise RuntimeError("pdftotext failed: " + r.stderr.decode("utf-8", "replace").strip()[:200])
    return r.stdout.decode("utf-8", "replace")


def find_gutter(lines):
    """Column index of the gutter in a two-column -layout page, or None."""
    body = [l.rstrip() for l in lines if len(l.strip()) > 0]
    long_lines = [l for l in body if len(l) > 60]
    if len(long_lines) < 15:
        return None
    width = max(len(l) for l in long_lines)
    best, best_score = None, 0.0
    for x in range(int(width * 0.3), int(width * 0.7)):
        ok = 0
        both = 0
        for l in long_lines:
            seg = l[x - 1:x + 2] if len(l) > x + 1 else "   "
            if seg.strip() == "":
                ok += 1
                if l[:x].strip() and l[x:].strip():
                    both += 1
        score = ok / len(long_lines)
        if score > best_score or (score == best_score and best is not None and abs(x - width / 2) < abs(best - width / 2)):
            best, best_score = x, score
            best_both = both
    if best is not None and best_score >= 0.85 and best_both >= 0.4 * len(long_lines):
        return best
    return None


def split_columns(page):
    lines = page.split("\n")
    g = find_gutter(lines)
    if g is None:
        return page, False
    left, right = [], []
    for l in lines:
        if len(l) <= g:
            left.append(l)
            right.append("")
            continue
        # Lines that run across the gutter with text (full-width) go to the left.
        if l[g - 1:g + 1].strip():
            left.append(l)
            right.append("")
            continue
        left.append(l[:g])
        right.append(l[g:])
    return "\n".join(x.rstrip() for x in left) + "\n" + "\n".join(x.strip() for x in right), True


def unlayout(text):
    """De-indent -layout text and reflow paragraphs; returns plain text and column count."""
    pages = text.split("\f")
    out_pages = []
    two = 0
    for pg in pages:
        t, was_two = split_columns(pg)
        two += was_two
        out_pages.append(t)
    return "\n\n".join(out_pages), two


PDF_HEAD_RE = re.compile(
    r"^(?:(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,3}|[A-H](?:\.\d{1,2}){0,3}|[IVX]{1,5}))\.?\s{1,8})?"
    r"(?P<title>[A-Z][A-Za-z0-9 ,:;&()'\-/]{1,90})$")
NAMED = {"abstract", "introduction", "related work", "background", "conclusion", "conclusions",
         "discussion", "references", "bibliography", "acknowledgements", "acknowledgments",
         "appendix", "limitations", "methods", "method", "results", "experiments",
         "broader impact", "impact statement", "ethics statement", "future work",
         "evaluation", "setup", "experimental setup", "summary", "contents"}


SMALLCAPS_RE = re.compile(r"\b([A-Z]) ([A-Z]{2,})")


def fix_smallcaps(s):
    """pdftotext renders small caps as 'S PARSE A UTOENCODERS'; join and title-case them."""
    letters = [c for c in s if c.isalpha()]
    if not letters or len(SMALLCAPS_RE.findall(s)) < 1:
        return s
    if sum(c.isupper() for c in letters) < 0.8 * len(letters):
        return s
    t = SMALLCAPS_RE.sub(r"\1\2", s)
    t = re.sub(r"([A-Z]) ?[-\u2010\u00ad] +([A-Z])", r"\1\2", t)
    small = {"of", "in", "and", "the", "for", "with", "on", "a", "an", "to", "is", "are", "at", "by"}
    out = []
    for k, w in enumerate(t.split(" ")):
        if not w:
            out.append(w)
        elif re.fullmatch(r"[A-Z]{2,3}s?", w) and w.lower() not in small and w not in (
                "THE", "AND", "FOR", "ARE", "OUT", "NOT", "TOP", "HOW", "WHY", "CAN", "NEW", "ONE", "TWO",
                "USE", "VIA", "ALL", "OUR", "BUT", "HAS", "WAS", "ITS", "WE", "OR", "AS", "IT", "BE", "DO",
                "IF", "SO", "UP", "NO", "VS"):
            out.append(w)  # acronym such as IOI, MLP, SAEs
        elif k > 0 and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def is_heading(line, prev_blank, next_line):
    s = line.strip()
    if not s or len(s) > 90 or len(s) < 3:
        return None
    m = PDF_HEAD_RE.match(s)
    if not m:
        return None
    num, title = m.group("num"), m.group("title").strip()
    # A bare name such as "Results" is a heading only after a blank line: the same
    # word can sit on a line of its own inside a paragraph or a table.
    if title.lower().rstrip(":") in NAMED and (num or prev_blank):
        return (num or "", title.rstrip(":"))
    if title.endswith("-"):
        return None
    nx = next_line.strip()
    if nx and nx[0].islower():
        return None
    if s.isupper() and len(words_of(s)) <= 8 and len(s) > 4 and prev_blank and not re.search(r"\d", s):
        return (num or "", title.title() if not num else title)
    if not num:
        return None
    if title.endswith((".", ",")) or len(title.split()) > 12:
        return None
    if re.fullmatch(r"[A-H]", num) and not s.startswith(num + " "):
        return None
    if re.fullmatch(r"[IVX]{1,5}", num) and not prev_blank:
        return None
    # Numbered heading: number then a capitalised short title; not "1 2 3" table rows.
    if re.search(r"\d", title[:3]) or sum(c.isdigit() for c in title) > len(title) * 0.3:
        return None
    if title.split()[0].lower() in ("figure", "table", "fig", "eq", "equation"):
        return None
    if len(num) <= 2 and num.isdigit() and int(num) > 20:
        return None
    return (num, title)


def words_of(s):
    return re.findall(r"[A-Za-z]+", s)


def from_pdf_text(text):
    """Sections from pdftotext output (with -layout columns already split)."""
    lines = text.split("\n")
    sections = [{"num": "", "title": "Front matter", "level": 1, "paras": []}]
    para = []
    seen_nums = set()
    last_top = 0
    appendix = [False]
    skip_next = [False]

    def flush():
        if para:
            t = "\n".join(para).strip()
            if t:
                sections[-1]["paras"].append(t)
            para.clear()

    prev_blank = True
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            flush()
            prev_blank = True
            continue
        if skip_next[0]:
            skip_next[0] = False
            continue
        line = fix_smallcaps(line)
        nxt = fix_smallcaps(lines[i + 1] if i + 1 < len(lines) else "")
        h = is_heading(line, prev_blank, nxt)
        if h:
            num, title = h
            ok = True
            if num:
                top = num.split(".")[0]
                if num in seen_nums:
                    ok = False
                elif top.isdigit() and appendix[0]:
                    ok = False
                elif top.isdigit() and last_top and int(top) not in (last_top, last_top + 1) and int(top) != 1:
                    ok = False
                if ok and top.isdigit():
                    last_top = int(top)
                if ok and re.fullmatch(r"[A-H]", top) and last_top:
                    appendix[0] = True
            if ok:
                flush()
                if num:
                    seen_nums.add(num)
                lvl = num.count(".") + 1 if num else 1
                # A heading that wraps onto a second small-caps line.
                raw_nx = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if (raw_nx and nxt.strip() != raw_nx and len(raw_nx) < 70
                        and not PDF_HEAD_RE.match(nxt.strip()).group("num") if PDF_HEAD_RE.match(nxt.strip()) else False):
                    title = title + " " + nxt.strip()
                    skip_next[0] = True
                sections.append({"num": num, "title": title, "level": lvl, "paras": []})
                prev_blank = True
                continue
        para.append(line.strip())
        prev_blank = False
    flush()
    # A line like "Abstract" may be glued: handle "Abstract—..." / "Abstract. ..." in front matter.
    fm = sections[0]
    if not any(s["title"].lower() == "abstract" for s in sections):
        for k, p in enumerate(fm["paras"]):
            m = re.match(r"^\s*(abstract)\s*[\.\-—:]\s*", p, re.I)
            if m:
                rest = [p[m.end():]] + fm["paras"][k + 1:]
                fm["paras"] = fm["paras"][:k]
                sections.insert(1, {"num": "", "title": "Abstract", "level": 1, "paras": rest})
                break
    figures, tables = [], []
    for s in sections:
        for p in s["paras"]:
            m = re.match(r"^(Figure|Fig\.|Table)\s+(\d+|[A-Z]\.\d+)\s*[:.]", p)
            if m:
                cap = " ".join(p.split())
                if m.group(1).startswith("Tab"):
                    tables.append({"caption": cap, "text": "", "section": label(s)})
                else:
                    figures.append({"caption": cap, "section": label(s)})
    return finish(sections, figures, tables)


def from_pdf(path):
    raw = pdftotext(path, layout=True)
    text, ncols = unlayout(raw)
    doc = from_pdf_text(text)
    doc["pdf_two_column_pages"] = ncols
    return doc


def from_plain(text):
    if re.search(r"^#{1,6} ", text, re.M):
        return from_markdown(text)
    return from_pdf_text(text)


def from_markdown(text):
    sections = [{"num": "", "title": "Front matter", "level": 1, "paras": []}]
    para = []
    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            if para:
                sections[-1]["paras"].append("\n".join(para).strip())
                para = []
            num, t = split_num(m.group(2))
            sections.append({"num": num, "title": t, "level": len(m.group(1)), "paras": []})
        elif not line.strip():
            if para:
                sections[-1]["paras"].append("\n".join(para).strip())
                para = []
        else:
            para.append(line)
    if para:
        sections[-1]["paras"].append("\n".join(para).strip())
    return finish(sections, [], [])
