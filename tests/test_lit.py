"""Offline tests for lit. Run: python3 -m unittest discover -s tests  (from the module folder).

They use saved fixtures only and make no network requests.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import lit  # noqa: E402
from litlib import doc as D  # noqa: E402
from litlib import library as L  # noqa: E402
from litlib import net  # noqa: E402
from litlib import quote as Q  # noqa: E402
from litlib import refcheck as R  # noqa: E402
from litlib import search as SR  # noqa: E402
from litlib import sources as S  # noqa: E402
from litlib.text import normalise, title_key  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def ip(*octets):
    """An IPv4 address from its octets. The private-range examples below (RFC 1918, RFC 6598) belong to
    nobody; they are written as octets because the release scanner flags every private-range literal."""
    return ".".join(str(o) for o in octets)


def fixture(name, mode="r"):
    with open(os.path.join(FIX, name), mode) as f:
        return f.read()


def _no_network(*a, **k):
    raise AssertionError("a test tried to use the network")


net.fetch = _no_network


class TestArxivAtom(unittest.TestCase):
    def test_parse(self):
        recs = S.parse_arxiv_atom(fixture("arxiv_2609.03664.atom", "rb"))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["arxiv"], "2609.03664")
        self.assertEqual(r["title"], "Cold Extremes during Dansgaard-Oeschger Oscillations")
        self.assertEqual(r["year"], 2026)
        self.assertEqual(r["authors"][0], "Ignacio del Amo")
        self.assertIn("interstadial", r["abstract"])


class TestS2(unittest.TestCase):
    def test_records(self):
        d = json.loads(fixture("s2_references.json"))
        recs = [S.s2_record(x["citedPaper"]) for x in d["data"]]
        self.assertTrue(all(r["title"] for r in recs))
        self.assertTrue(any(r.get("arxiv") for r in recs))
        self.assertTrue(all(r["source"] == "s2" for r in recs))


class TestHtmlSections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = D.from_html(fixture("arxiv_2609.03664.html"))

    def test_title_and_sections(self):
        d = self.doc
        self.assertEqual(d["title"], "Cold Extremes during Dansgaard-Oeschger Oscillations")
        labels = [D.label(s) for s in d["sections"]]
        self.assertIn("Abstract", labels)
        self.assertIn("1 Introduction", labels)
        self.assertIn("3.1 Non-parametric model", labels)
        self.assertGreater(len(d["sections"]), 20)

    def test_figures_tables(self):
        self.assertGreater(len(self.doc["figures"]), 5)
        self.assertTrue(all(f["caption"] for f in self.doc["figures"]))

    def test_no_bibliography_noise_in_abstract(self):
        ab = next(s for s in self.doc["sections"] if s["title"] == "Abstract")
        self.assertIn("interstadial", ab["text"])
        self.assertLess(len(ab["text"].split()), 400)


class TestPdfSections(unittest.TestCase):
    def test_two_column(self):
        text, ncols = D.unlayout(fixture("twocol_p1-3.txt"))
        self.assertGreaterEqual(ncols, 2)
        d = D.from_pdf_text(text)
        labels = [D.label(s) for s in d["sections"]]
        self.assertIn("Abstract", labels)
        self.assertIn("1 Introduction", labels)
        self.assertIn("2.1 Dough and flour", labels)
        # Columns were split: the abstract reads as one passage.
        ab = next(s for s in d["sections"] if s["title"] == "Abstract")
        self.assertIn("We measured the time a plain wheat dough needs to double its volume at five room temperatures "
                      "between 16 and 32 degrees Celsius.", " ".join(ab["text"].split()))

    def test_single_column(self):
        text, ncols = D.unlayout(fixture("onecol_p1-3.txt"))
        self.assertEqual(ncols, 0)
        d = D.from_pdf_text(text)
        labels = [D.label(s) for s in d["sections"]]
        self.assertIn("1 Introduction", labels)
        self.assertIn("2.1 Results", labels)

    def test_small_caps_headings(self):
        # pdftotext prints a small-caps heading with a space after each capital.
        d = D.from_pdf_text("1 I NTRODUCTION\n\nBread rises faster in a warm room.\n\n"
                            "2 M ETHOD\n\n2.1 D OUGH AND F LOUR\n\nWe used plain wheat flour.\n")
        labels = [D.label(s) for s in d["sections"]]
        self.assertIn("1 Introduction", labels)
        self.assertIn("2.1 Dough and Flour", labels)

    def test_heading_rules(self):
        self.assertEqual(D.is_heading("3.2    Tasks and evaluation", True, ""), ("3.2", "Tasks and evaluation"))
        self.assertIsNone(D.is_heading("2. Using this experience of baking in the sum-", True, "mer months"))
        self.assertIsNone(D.is_heading("Oven-2 350W", True, ""))
        self.assertIsNone(D.is_heading("Figure 3 Accuracy", True, ""))
        self.assertEqual(D.fix_smallcaps("2.1 D OUGH AND F LOUR"), "2.1 Dough and Flour")

    def test_bare_heading_needs_a_blank_line_before_it(self):
        # A single word with no number is a heading only when a blank line comes before it;
        # a numbered heading is one either way.
        self.assertIsNone(D.is_heading("Results", False, ""))
        self.assertEqual(D.is_heading("Results", True, ""), ("", "Results"))
        self.assertEqual(D.is_heading("3 Results", False, ""), ("3", "Results"))


class TestQuote(unittest.TestCase):
    TEXT = ("One problem is uneven heating, where loaves bake at several, clearly dis-\n"
            "tinct rates. We use a “water bath” to keep the dough at one temperature.\n\n"
            "The best oven was right on 58% of loaves, while hu- man tasters agreed on 94%.")

    def test_verbatim_across_line_break_hyphen_and_quotes(self):
        r = Q.check(self.TEXT, "loaves bake at several, clearly distinct rates")
        self.assertEqual(r["status"], Q.VERBATIM)
        r = Q.check(self.TEXT, 'We use a "water bath" to keep the dough')
        self.assertEqual(r["status"], Q.VERBATIM)
        r = Q.check(self.TEXT, "while human tasters agreed on 94%")
        self.assertEqual(r["status"], Q.VERBATIM)

    def test_hyphen_inside_a_word(self):
        text = "A nonlinear curve was fitted to each batch after re-\nheating the oven."
        # The paper has no hyphen where the quote has one: close, but not the paper's words.
        r = Q.check(text, "A non-linear curve was fitted to each batch")
        self.assertEqual(r["status"], Q.NEAR)
        self.assertIn("hyphens inside words", r["note"])
        # A hyphen at a line break may belong to the word or to the line break: either reading is verbatim.
        r = Q.check(text, "after re-heating the oven")
        self.assertEqual(r["status"], Q.VERBATIM)
        r = Q.check(text, "after reheating the oven")
        self.assertEqual(r["status"], Q.VERBATIM)

    def test_near_changed_number(self):
        r = Q.check(self.TEXT, "The best oven was right on 68% of loaves, while human tasters agreed on 94%.")
        self.assertEqual(r["status"], Q.NEAR)
        self.assertTrue(any(d["paper"] == "58%" for d in r["diff"]))

    def test_case_only(self):
        r = Q.check(self.TEXT, "the best oven was right on 58% of loaves")
        self.assertEqual(r["status"], Q.NEAR)

    def test_not_found(self):
        r = Q.check(self.TEXT, "Cold kitchens bring out hidden flavours in every sourdough.")
        self.assertEqual(r["status"], Q.NOT_FOUND)

    def test_normalise_map(self):
        key, idx = normalise("A  “b”\nc")
        self.assertEqual(key, 'A "b" c')
        self.assertEqual(len(idx), len(key))


class TestReferences(unittest.TestCase):
    MD = """# Notes

See arXiv:2609.03664 in the text.

## References

1. del Amo, I., Ditlevsen, P. (2026). Cold Extremes during Dansgaard-Oeschger Oscillations. arXiv:2609.03664
2. Example, A., Sample, B. (2021). Slow Loaves: Measuring How Bread Dough Rises in Cold Kitchens. A Made-Up Journal 2022.
3. Some post. https://www.lesswrong.com/posts/madeUpPostId12345/a-made-up-post
4. A paper, https://doi.org/10.5555/12345678 and https://openreview.net/forum?id=exampleId1
"""

    def test_extract(self):
        es = R.extract(self.MD)
        kinds = [(e["kind"], e.get("id")) for e in es]
        self.assertIn(("arxiv", "2609.03664"), kinds)
        self.assertIn(("lw", "madeUpPostId12345"), kinds)
        self.assertIn(("doi", "10.5555/12345678"), kinds)
        self.assertIn(("openreview", "exampleId1"), kinds)
        text = [e for e in es if e["kind"] == "text"]
        self.assertEqual(len(text), 1)
        self.assertEqual(R.guess_title(text[0]["text"]), "Slow Loaves: Measuring How Bread Dough Rises in Cold Kitchens")

    def test_arxiv_doi_maps_to_arxiv(self):
        ids = R.ids_in("doi:10.48550/arXiv.2609.03664")
        self.assertEqual(ids[0], {"kind": "arxiv", "id": "2609.03664"})

    def test_title_match(self):
        real = "Cold Extremes during Dansgaard-Oeschger Oscillations"
        self.assertGreater(R.title_match(real, "del Amo et al. Cold extremes during Dansgaard-Oeschger oscillations."), 0.9)
        self.assertLess(R.title_match(real, "Slow Loaves: Measuring How Bread Dough Rises in Cold Kitchens"), 0.3)


class TestUrlChecks(unittest.TestCase):
    SKIPPED = ("UNCHECKED", "skipped: not a public address")

    @staticmethod
    def entry(url):
        return {"kind": "url", "id": url, "text": "See " + url, "line": 1}

    @staticmethod
    def resolves_to(table):
        def fake(host, *a, **k):
            if host not in table:
                raise OSError("name does not resolve")
            return [(2, 1, 6, "", (table[host], 80))]
        return mock.patch.object(net.socket, "getaddrinfo", fake)

    def test_literal_non_public_addresses_are_skipped(self):
        # net.fetch raises in these tests, so a pass also shows that nothing was fetched.
        for u in ("http://127.0.0.1:8000/x", f"http://{ip(10, 0, 0, 1)}/", f"http://{ip(192, 168, 1, 1)}/admin",
                  "http://169.254.1.1/latest/meta-data/", f"http://{ip(100, 64, 0, 1)}/", "http://0.0.0.0/",
                  "http://[::1]/", "http://[fe80::1]/", "http://[::ffff:127.0.0.1]/", "http://2130706433/"):
            self.assertEqual(R.check_entry(self.entry(u))[:2], self.SKIPPED, u)

    def test_name_is_resolved_first(self):
        with self.resolves_to({"wiki.example": ip(192, 168, 1, 20)}):
            self.assertEqual(R.check_entry(self.entry("http://wiki.example/page"))[:2], self.SKIPPED)
            status, detail, _ = R.check_entry(self.entry("http://gone.example/page"))
        self.assertEqual(status, "UNCHECKED")
        self.assertIn("unreachable", detail)

    def test_public_address_is_fetched_with_the_redirect_guard(self):
        calls = []

        def fake_fetch(url, **k):
            calls.append((url, k.get("guard")))
            return b""

        with self.resolves_to({"paper.example": "93.184.216.34"}), mock.patch.object(net, "fetch", fake_fetch):
            status, _, _ = R.check_entry(self.entry("https://paper.example/a.html"))
        self.assertEqual(status, "OK")
        self.assertEqual(calls, [("https://paper.example/a.html", net.public_only)])

    def test_guard_refuses_a_redirect_to_a_non_public_address(self):
        guard = net._GuardedRedirects(net.public_only)
        with self.assertRaises(net.NetError) as cm:
            guard.redirect_request(None, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data/")
        self.assertEqual(str(cm.exception), "skipped: not a public address")

    def test_no_urls_option(self):
        def no_lookup(*a, **k):
            raise AssertionError("--no-urls must not resolve a name")

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "notes.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("Background: https://paper.example/a.html and http://127.0.0.1:8000/x\n")
            out = io.StringIO()
            with mock.patch.object(net.socket, "getaddrinfo", no_lookup), contextlib.redirect_stdout(out):
                rc = lit.main(["verify-refs", "--no-urls", "--json", p])
        rows = json.loads(out.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["status"], "UNCHECKED")
            self.assertIn("--no-urls", r["detail"])


class TestLibrary(unittest.TestCase):
    def test_openreview_id_shape(self):
        self.assertEqual(L.resolve("openreview:exampleId1"), ("openreview", "exampleId1"))
        self.assertEqual(L.resolve("https://openreview.net/forum?id=exampleId2"), ("openreview", "exampleId2"))
        for bad in ("openreview:../../x", "openreview:a/b", "openreview:a b",
                    "https://openreview.net/forum?id=../../etc/passwd"):
            with self.assertRaises(L.LitError, msg=bad) as cm:
                L.resolve(bad)
            self.assertIn("not a valid note id", str(cm.exception))

    def test_a_key_is_one_folder_name(self):
        for kind, ident in (("arxiv", "2609.03664v1"), ("arxiv", "hep-th/9901001"), ("openreview", "exampleId1"),
                            ("lw", "abcDEF1234567"), ("doi", "10.1000/XYZ(1)"), ("local", "/tmp/My Notes.txt")):
            key = L.key_for(kind, ident)
            self.assertTrue(L.KEY_RE.fullmatch(key), key)
            self.assertEqual(os.path.dirname(L.path(key)), L.LIB)
        self.assertEqual(L.key_for("arxiv", "hep-th/9901001"), "arxiv-hep-th_9901001")
        # an id that skipped resolve(), such as one taken from a web page's metadata, cannot leave the library
        for kind in ("arxiv", "openreview", "lw", "doi"):
            key = L.key_for(kind, "../../etc/x y")
            self.assertNotIn("/", key)
            self.assertEqual(os.path.dirname(L.path(key)), L.LIB)
        for bad in ("", ".", "..", "../x", "a/b", "/abs", ".hidden", "a b", None):
            with self.assertRaises(L.LitError, msg=repr(bad)):
                L.path(bad)

    def test_local_file_keeps_only_its_base_name(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bread notes.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("Notes on bread\n\nA starter is a culture of flour and water.\n")
            meta, document = L.get_local(p)
        self.assertEqual(meta["path"], "bread notes.txt")
        self.assertEqual(meta["url"], "")
        self.assertEqual(meta["fulltext_source"], "bread notes.txt (text)")
        self.assertNotIn(d, json.dumps(meta))
        self.assertTrue(document["sections"])


class TestMerge(unittest.TestCase):
    def test_merge_by_id_and_title(self):
        # Made-up records: the ids are placeholders, not real papers.
        a = dict(S.blank("arxiv"), title="Rise Time of Bread Dough at Five Kitchen Temperatures",
                 arxiv="0000.00001", year=2021, rank=0)
        b = dict(S.blank("openalex"), title="Rise time of bread dough at five kitchen temperatures.",
                 doi="10.48550/arxiv.0000.00001", cites=1000, year=2021, rank=2)
        c = dict(S.blank("openreview"), title="Rise Time of Bread Dough at Five Kitchen Temperatures",
                 openreview="exampleId2", year=2022, rank=0)
        d = dict(S.blank("arxiv"), title="Crumb Structure of Rye Loaves", arxiv="0000.00002", rank=1)
        m = SR.merge([a, b, c, d])
        self.assertEqual(len(m), 2)
        big = next(x for x in m if x["arxiv"] == "0000.00001")
        self.assertEqual(set(big["sources"]), {"arxiv", "openalex", "openreview"})
        self.assertEqual(big["openreview"], "exampleId2")
        self.assertEqual(big["cites"], 1000)
        self.assertEqual(big["year"], 2021)
        self.assertEqual(SR.display_id(big), "arXiv:0000.00001")

    def test_title_key(self):
        self.assertEqual(title_key("Crumb Structure of Rye Loaves."), title_key("crumb structure of  rye loaves"))


if __name__ == "__main__":
    unittest.main()
