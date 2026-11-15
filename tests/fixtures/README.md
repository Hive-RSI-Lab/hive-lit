# Test fixtures

Saved copies of public pages, one made-up paper and one made-up API reply, so that the tests run without a network. The real paper is from climate physics and the made-up one is about bread dough: the subjects were picked only for the shapes the parser has to read (an arXiv HTML page with numbered sections and captioned figures, a one-column PDF, a two-column PDF).

- `arxiv_2609.03664.atom`: arXiv API metadata record (title, authors, abstract) for arXiv 2609.03664. arXiv metadata is CC0 1.0. Source: https://export.arxiv.org/api/query?id_list=2609.03664&max_results=1
- `arxiv_2609.03664.html`: arXiv HTML version (v1) of Ignacio del Amo and Peter Ditlevsen, "Cold Extremes during Dansgaard-Oeschger Oscillations", 2026, arXiv 2609.03664. Licence: CC BY 4.0. Source: https://arxiv.org/html/2609.03664v1 (the paper's page is https://arxiv.org/abs/2609.03664)
- `onecol_p1-3.txt`: pages 1 to 3 of the PDF of the same paper (arXiv 2609.03664v1), as text. Licence: CC BY 4.0. Source: https://arxiv.org/pdf/2609.03664v1
- `twocol.tex`: a made-up two-column paper, "Rise Time of Bread Dough at Five Kitchen Temperatures" by A. Example and B. Sample, written for these tests. No real data: every number in it is invented and the authors do not exist.
- `twocol_p1-3.txt`: pages 1 to 3 of the PDF built from `twocol.tex`, as text.
- `s2_references.json`: six made-up records in the shape of a Semantic Scholar Academic Graph API reply (`data[].citedPaper` with title, authors, year, venue, ids and citation count). No real data. API: https://www.semanticscholar.org/product/api

Changes to the originals: the one author e-mail address in `arxiv_2609.03664.html` and in `onecol_p1-3.txt` is replaced with `author@example.com`. `arxiv_2609.03664.html` holds the paper only, that is arXiv's licence box and the `<article>` element: arXiv's own page header, footer, scripts and styles are cut (they are not part of the CC BY work). Nothing else is changed, and the `.atom` file is the API's reply as it came.

The two `.txt` files were made with `pdftotext -enc UTF-8 -layout -f 1 -l 3`, the same call lit uses (pdftotext 26.01.0).

To build the two-column file again (pdfTeX 1.40.28 from TeX Live 2025; another version may break lines elsewhere, and the tests then need the new text checked against them):

```sh
pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error twocol.tex
pdflatex -no-shell-escape -interaction=nonstopmode -halt-on-error twocol.tex
pdftotext -enc UTF-8 -layout -f 1 -l 3 twocol.pdf twocol_p1-3.txt
```

Licence texts: CC BY 4.0 is at https://creativecommons.org/licenses/by/4.0/ and CC0 1.0 at https://creativecommons.org/publicdomain/zero/1.0/
