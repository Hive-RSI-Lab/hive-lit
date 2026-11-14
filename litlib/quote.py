"""Check whether a quote is in a text: VERBATIM, NEAR or NOT FOUND."""
import difflib
import re

from .text import SOFT, normalise

VERBATIM, NEAR, NOT_FOUND = "VERBATIM", "NEAR", "NOT FOUND"


def _sentence_around(text, start, end):
    """Expand [start, end) in text to sentence boundaries."""
    a = start
    while a > 0:
        if text[a - 1] in ".!?" and (a >= len(text) or text[a].isspace()) and a < start:
            break
        if text[a - 1:a + 1] == "\n\n":
            break
        a -= 1
        if start - a > 600:
            break
    b = max(start, end - 1)
    while b < len(text):
        if text[b] in ".!?" and b >= end - 1 and (b + 1 >= len(text) or text[b + 1].isspace()):
            b += 1
            break
        if text[b:b + 2] == "\n\n":
            break
        b += 1
        if b - end > 600:
            break
    return " ".join(text[a:b].split())


def _wordspans(key):
    return [(m.start(), m.end()) for m in re.finditer(r"\S+", key)]


def _find_across_breaks(text, quote):
    """(start, end) of quote in text, letting each line-break hyphen of the text be a hyphen or nothing."""
    tkey, tidx = normalise(text, soft=True)
    qkey = normalise(quote, soft=True)[0].replace(SOFT, "")
    if not qkey:
        return None
    parts = []
    prev = ""
    for c in qkey:
        if c == "-":
            parts.append("[-%s]" % SOFT)
        else:
            if prev.isalpha() and c.isalpha():
                parts.append(SOFT + "?")
            parts.append(re.escape(c))
        prev = c
    m = re.search("".join(parts), tkey)
    if not m:
        return None
    return tidx[m.start()], tidx[m.end() - 1] + 1


def check(text, quote, near_threshold=0.75):
    """Return a dict: status, start, end (in text), context, note, diff."""
    q = quote.strip().strip('"').strip("“”").strip()
    qkey, _ = normalise(q)
    tkey, tidx = normalise(text)
    if not qkey:
        return {"status": NOT_FOUND, "note": "empty quote"}
    # 1. exact after normalisation
    pos = tkey.find(qkey)
    if pos >= 0:
        s, e = tidx[pos], tidx[pos + len(qkey) - 1] + 1
        return {"status": VERBATIM, "start": s, "end": e,
                "context": _sentence_around(text, s, e), "note": ""}
    # A hyphen that ends a line in the paper may belong to the word or not
    # ("non-" / "linear" is "nonlinear" or "non-linear"): both spellings are verbatim.
    hit = _find_across_breaks(text, q)
    if hit:
        s, e = hit
        return {"status": VERBATIM, "start": s, "end": e,
                "context": _sentence_around(text, s, e),
                "note": "matched across a line-break hyphen"}
    # The same words but for hyphens inside them ("non-linear" for "nonlinear"): not verbatim.
    qk2, _ = normalise(q, drop_hyphens=True)
    tk2, tidx2 = normalise(text, drop_hyphens=True)
    p2 = tk2.find(qk2) if qk2 else -1
    if p2 >= 0:
        s, e = tidx2[p2], tidx2[p2 + len(qk2) - 1] + 1
        return {"status": NEAR, "start": s, "end": e, "ratio": 0.99,
                "context": _sentence_around(text, s, e),
                "passage": " ".join(text[s:e].split()),
                "note": "differs only in hyphens inside words", "diff": []}
    # 2. case-insensitive
    lpos = tkey.lower().find(qkey.lower())
    if lpos >= 0:
        s, e = tidx[lpos], tidx[lpos + len(qkey) - 1] + 1
        return {"status": NEAR, "start": s, "end": e, "ratio": 0.99,
                "context": _sentence_around(text, s, e),
                "passage": text[s:e], "note": "differs only in letter case",
                "diff": []}
    # 3. fuzzy, word level
    tw_spans = _wordspans(tkey)
    tw = [_w(tkey[a:b]) for a, b in tw_spans]
    qw = [_w(x) for x in qkey.split()]
    n = len(qw)
    if n == 0 or not tw:
        return {"status": NOT_FOUND, "note": ""}
    qset = {}
    for k, w in enumerate(qw):
        qset.setdefault(w, []).append(k)
    votes = {}
    for i, w in enumerate(tw):
        for k in qset.get(w, ()):
            st = i - k
            votes[st] = votes.get(st, 0) + 1
    if not votes:
        return {"status": NOT_FOUND, "note": "no word of the quote matches"}
    # Merge nearby starts.
    cand = sorted(votes.items(), key=lambda kv: -kv[1])[:40]
    best = (0.0, None, None)
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(qw)
    tried = set()
    for st, _ in cand:
        for d in (-2, -1, 0, 1, 2):
            for ln in (n - 2, n - 1, n, n + 1, n + 2):
                a = max(0, st + d)
                b = min(len(tw), a + max(1, ln))
                if (a, b) in tried:
                    continue
                tried.add((a, b))
                sm.set_seq1(tw[a:b])
                r = sm.ratio()
                if r > best[0]:
                    best = (r, a, b)
    r, a, b = best
    if a is None or r < near_threshold or n < 3 and r < 1:
        res = {"status": NOT_FOUND, "ratio": round(r, 3)}
        if a is not None and r >= 0.5:
            s, e = tidx[tw_spans[a][0]], tidx[tw_spans[b - 1][1] - 1] + 1
            res["closest"] = " ".join(text[s:e].split())
        return res
    s, e = tidx[tw_spans[a][0]], tidx[tw_spans[b - 1][1] - 1] + 1
    passage_words = tkey[tw_spans[a][0]:tw_spans[b - 1][1]].split()
    sm2 = difflib.SequenceMatcher(None, [_w(x) for x in passage_words], qw, autojunk=False)
    diff = []
    for op, i1, i2, j1, j2 in sm2.get_opcodes():
        if op == "equal":
            continue
        diff.append({"op": op, "paper": " ".join(passage_words[i1:i2]),
                     "quote": " ".join(qkey.split()[j1:j2])})
    return {"status": NEAR, "start": s, "end": e, "ratio": round(r, 3),
            "passage": " ".join(text[s:e].split()),
            "context": _sentence_around(text, s, e), "diff": diff, "note": ""}


def _w(x):
    return re.sub(r"[^\w]", "", x.lower()) or x.lower()
