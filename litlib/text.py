"""Text helpers: tokenising, normalising for matching, title keys."""
import re
import unicodedata

STOP = set("""a an the of in on for to and or with by from at as is are was were be been
being that this these those it its into via using use used about over under than then
their there what which who whom how why when where does do did can could should would
we our you your they them he she his her not no paper papers work study approach method
methods new towards toward based between more most also such any all some each other one
two three first second i me my find show shows showing found finding about across within
""".split())

_WORD = re.compile(r"[A-Za-z0-9]+")


def words(s):
    return [w.lower() for w in _WORD.findall(s or "")]


def stem(w):
    for suf in ("ization", "isation", "ations", "ation", "ings", "ing", "ies", "ers", "er",
                "es", "ed", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def title_key(t):
    return " ".join(words(t))


# Characters folded before comparing quotes.
_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"', "`": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...", "­": "", "​": "", "‌": "", "‍": "", "﻿": "",
}


def fold_char(c):
    if c in _FOLD:
        return _FOLD[c]
    if ord(c) < 128:
        return c
    return unicodedata.normalize("NFKC", c)


# Stands for a hyphen that ends a line: the word may be written with or without it.
SOFT = "\x01"


def normalise(text, drop_hyphens=False, soft=False):
    """Return (key, idx): a folded string and, for each key char, its index in text.

    Folds ligatures (NFKC), quote marks, dashes and all whitespace runs to one
    space; joins words hyphenated across a line break ("inter-\npretable").
    With drop_hyphens, every hyphen is removed and a space beside it too.
    With soft, a hyphen at a line break becomes SOFT instead of vanishing, and
    "inter- pretable" (the same break after the lines were joined) counts too.
    """
    out = []
    idx = []
    n = len(text)
    i = 0
    pending_space = -1
    while i < n:
        c = text[i]
        if c == SOFT:
            i += 1
            continue
        f = fold_char(c)
        if f == "-":
            # hyphen at a line break: '-' then whitespace containing a newline
            j = i + 1
            while j < n and text[j] in " \t\r\f":
                j += 1
            at_break = j < n and text[j] == "\n"
            joined = soft and j > i + 1 and pending_space < 0
            if (at_break or joined) and out and out[-1].isalpha():
                while j < n and text[j].isspace():
                    j += 1
                if j < n and text[j].isalpha() and text[j].islower():
                    if soft and pending_space < 0:
                        out.append(SOFT)
                        idx.append(i)
                    i = j
                    continue
            if drop_hyphens:
                if out and out[-1] == " ":
                    out.pop()
                    idx.pop()
                pending_space = -1
                i += 1
                while i < n and text[i].isspace():
                    i += 1
                continue
        if f.isspace() or f == "":
            if f == "":
                i += 1
                continue
            if out and pending_space < 0:
                pending_space = i
            i += 1
            continue
        if pending_space >= 0:
            out.append(" ")
            idx.append(pending_space)
            pending_space = -1
        for ch in f:
            if ch.isspace():
                ch = " "
            out.append(ch)
            idx.append(i)
        i += 1
    return "".join(out), idx
