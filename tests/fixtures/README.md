# Test fixtures

Saved copies of public pages and one made-up API reply, so that the tests run without a network.

- `arxiv_2309.08600.atom`: arXiv API metadata record (title, authors, abstract) for arXiv 2309.08600. arXiv metadata is CC0 1.0. Source: https://export.arxiv.org/api/query?id_list=2309.08600
- `arxiv_2309.08600.html`: arXiv HTML version (v3) of Hoagy Cunningham, Aidan Ewart, Logan Riggs, Robert Huben and Lee Sharkey, "Sparse Autoencoders Find Highly Interpretable Features in Language Models", 2023, arXiv 2309.08600. Licence: CC BY 4.0. Source: https://arxiv.org/abs/2309.08600
- `onecol_p1-3.txt`: pages 1 to 3 of the PDF of the same paper (arXiv 2309.08600v3), as text. Licence: CC BY 4.0. Source: https://arxiv.org/pdf/2309.08600v3
- `twocol_p1-3.txt`: pages 1 to 3, as text, of John Hewitt and Christopher D. Manning, "A Structural Probe for Finding Syntax in Word Representations", NAACL 2019, pages 4129-4138, ACL Anthology N19-1419, DOI 10.18653/v1/N19-1419. Licence: CC BY 4.0. Source: https://aclanthology.org/N19-1419/
- `s2_references.json`: six made-up records in the shape of a Semantic Scholar Academic Graph API reply (`data[].citedPaper` with title, authors, year, venue, ids and citation count). No real data. API: https://www.semanticscholar.org/product/api

Changes to the originals: the author e-mail addresses in the three paper files are replaced with `example.com` placeholders, and two control characters are removed from `twocol_p1-3.txt`. `arxiv_2309.08600.html` holds the paper only: arXiv's own page header, footer, scripts and styles are cut (they are not part of the CC BY work), and the DOI of one bibliography entry (the 1975 *Biol. Cybern.* article) is removed, because the release scanner reads its suffix as an account ID. The two `.txt` files were made with `pdftotext -enc UTF-8 -layout -f 1 -l 3`, the same call lit uses.

Licence texts: CC BY 4.0 is at https://creativecommons.org/licenses/by/4.0/ and CC0 1.0 at https://creativecommons.org/publicdomain/zero/1.0/
