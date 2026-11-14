# lit

A command-line tool for ML and AI-safety papers. It searches several sources, fetches the full text, and lets you read a paper by section. It can also check a quote or a reference list against the real paper.

Use it before you cite. It helps you avoid three mistakes: citing a paper that does not exist, giving a claim to the wrong paper, and quoting words the paper does not contain.

It uses only the Python standard library and needs no API keys.

## Install

lit needs Python 3.10 or newer, and `pdftotext` from poppler-utils to read PDFs. There is nothing to build.

```
python3 /path/to/lit/lit.py search "sparse autoencoders"   # run it where it is
ln -s /path/to/lit/lit.py ~/.local/bin/lit                 # or link it as `lit` in a folder on your PATH
```

Inside a Hive, `hive module add lit` links the command for you.

## Quick start

```
lit search "sparse autoencoders interpretable features"     # find papers
lit get 2309.08600                                         # fetch full text
lit toc 2309.08600                                         # list the sections
lit read 2309.08600 --section 3.1                          # read one section
lit quote 2309.08600 "the exact words you want to quote"   # check a quote
```

A paper can be named by an arXiv id, `arXiv:ID`, a DOI, an arXiv/OpenReview/LessWrong URL, `lw:POSTID`, `af:POSTID`, or its library key (see `lit list`).

## Recipes

### Find related work

```
lit search "chain of thought faithfulness" --year 2023- --n 15
lit refs 2307.13702 --n 30                  # what this paper cites
lit cites 2307.13702 --sort year            # newer papers that cite it
```

Search asks arXiv, Semantic Scholar, OpenAlex, OpenReview, LessWrong and Crossref at the same time. It merges duplicates and ranks the results. Use `-s lw,af` to search LessWrong and the Alignment Forum only. Add `--json` for machine-readable output.

### Read a paper by section

```
lit get 2309.08600
lit toc 2309.08600
lit read 2309.08600 --abstract
lit read 2309.08600 --section 2 --max-words 800
lit read 2309.08600 --section "Introduction"
lit read 2309.08600 --figures
lit grep 2309.08600 "dictionary size" -C 1
lit grep --all "superposition"              # search every paper you have fetched
```

For arXiv papers, lit uses the arXiv HTML version when it exists. Otherwise it uses the PDF text from `pdftotext`. For a DOI with no arXiv version, `lit get` may download the PDF from the publisher host that OpenAlex names as the open copy, and every PDF is parsed by the `pdftotext` on your own machine. You can add a local file with `lit add paper.pdf`; lit keeps the file's name but not the folder it came from.

### Check a quote before using it

```
lit quote 2309.08600 "the exact words"
```

- `VERBATIM` (exit 0): the words are in the paper. Line breaks, hyphens at a line break and curly quotes are ignored.
- `NEAR` (exit 1): something close is in the paper. lit shows the real passage and what differs. Quote the real passage instead. A quote that differs only by a hyphen inside a word (`non-linear` for the paper's `nonlinear`) is also `NEAR`.
- `NOT FOUND` (exit 2): do not use the quote.

### Check my reference list

```
lit verify-refs draft.md
```

lit reads each reference, id and URL in the file and looks it up.

- `OK`: the work exists and its title matches.
- `MISMATCH`: the id exists but points to a different title. Usually this is a wrong arXiv number.
- `NOT FOUND`: lit cannot find the work. Treat it as invented until you find it yourself.
- `UNCHECKED`: lit could not check the entry, for example after a network error or for a URL it does not fetch.

A plain URL is fetched only when its host is a public address. A URL that points to your own machine or to a private network is not fetched and is reported as `skipped: not a public address`. With `--no-urls`, lit fetches no plain URL at all; arXiv ids, DOIs and the other known ids are still looked up.

The exit code is 0 if all are OK. To build a correct reference, use `lit cite 2309.08600 --style bibtex`.

## Files

- Library: `~/.local/share/lit/`. Cache: `~/.cache/lit/`, kept for 24 hours.
- Tests: `python3 -m unittest discover -s tests` (offline).
- Search battery: `battery/run.py` runs 40 known-item queries through `lit search` and counts how often the target paper is found. Only lit is needed. If a `research` command is on your PATH, `--tools lit,research` runs it on the same queries as a comparison.
- Test fixtures: `tests/fixtures/README.md` lists the source and licence of each saved file.

## Limits

- Semantic Scholar often answers 429 (rate limited) when there is no key. lit then uses OpenAlex for `refs` and `cites`. OpenAlex gives at most the top 200 citing papers.
- OpenReview search works. OpenReview pages and PDFs are behind a bot check, so `lit get openreview:ID` fails. Use the arXiv version, or download the PDF yourself and use `lit add`.
- PDF section headings are found by rules, not by layout. Most papers parse well, but some headings are missed or split. Check `lit toc` first.
- Two-column PDFs are split into columns. Tables and equations from PDFs are rough.
- `quote` checks words only. It cannot tell you whether the quote is used in its true sense.
- A reference with no id is matched by title only. Two papers with the same title can be confused.
- LessWrong and Alignment Forum posts have no sections unless the post uses headings.
