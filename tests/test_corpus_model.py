"""Tests for tools/corpus/model.py: the section model every source parses into.

The properties that matter downstream: the content hash moves when the
LAW moves and only then (so the quarterly refresh reports real amendments,
not publisher churn), chunks never cut a paragraph (so the citation
guard's verbatim check can find a quoted subdivision intact), and the
"last amended" date is read from the history, never guessed.
"""

import hashlib
import os
import unittest

from tools.corpus.alp import parse_chapter
from tools.corpus.model import (
    MAX_CHUNK_CHARS,
    Section,
    latest_effective_date,
    law_hash,
    pack_paragraphs,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alp")


def _section(**overrides) -> Section:
    fields = dict(
        source_key="nyc-hmc",
        authority="NYC Admin Code",
        citation="27-2029",
        title="Minimum temperature to be maintained",
        heading_path="Chapter 2: Housing Maintenance Code",
        paragraphs=["a. First paragraph.", "(1) Second paragraph."],
        official_url="https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-60410",
    )
    fields.update(overrides)
    return Section(**fields)


def _real(file_id: str, citation: str) -> Section:
    with open(os.path.join(FIXTURES, f"{file_id}.xml"), "rb") as handle:
        result = parse_chapter(handle.read(), source_key="test", authority="NYC Admin Code")
    return {s.citation: s for s in result.sections}[citation]


class SectionKeyTests(unittest.TestCase):
    def test_key_includes_the_source(self):
        self.assertEqual(_section().section_key, "nyc-hmc:27-2029")

    def test_the_two_real_26_1301s_get_different_keys(self):
        # Title 26 has two Chapter 13s, each with a § 26-1301.
        rtc = _section(source_key="nyc-rtc", citation="26-1301")
        rent_payment = _section(source_key="nyc-t26-ch13-rent-payment", citation="26-1301")
        self.assertNotEqual(rtc.section_key, rent_payment.section_key)


class ContentHashTests(unittest.TestCase):
    def test_history_and_notes_do_not_change_the_hash(self):
        bare = _section()
        annotated = _section(
            history=["(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)"],
            notes=["Editor's note: see Appendix A."],
        )
        self.assertEqual(bare.content_hash, annotated.content_hash)

    def test_whitespace_does_not_change_the_hash(self):
        a = _section(paragraphs=["a. First  paragraph.", "(1) Second paragraph."])
        b = _section(paragraphs=["a. First paragraph.  ", "  (1)\tSecond paragraph."])
        self.assertEqual(a.content_hash, b.content_hash)

    def test_an_amendment_changes_the_hash(self):
        # The pre-2017 overnight rule vs. the current one: exactly the change
        # a stale mirror misses and the refresh must catch.
        old = _section(paragraphs=["(2) ... at least fifty-five degrees Fahrenheit whenever ..."])
        new = _section(paragraphs=["(2) ... at least sixty-two degrees Fahrenheit."])
        self.assertNotEqual(old.content_hash, new.content_hash)

    def test_only_ascii_whitespace_is_normalized(self):
        # Must match corpus_law_hash() in the 20260929 migration, which the
        # database uses to reject any write whose hash disagrees. U+00A0 is
        # the publisher's character, not formatting.
        self.assertEqual(law_hash(" a \t\n\x0b\x0c\r b "), law_hash("a b"))
        self.assertNotEqual(law_hash("a\u00a0b"), law_hash("a b"))
        self.assertEqual(law_hash("a b"), hashlib.sha256(b"a b").hexdigest())

    def test_hash_is_sha256_hex(self):
        digest = _section().content_hash
        self.assertEqual(len(digest), 64)
        int(digest, 16)


class PackParagraphsTests(unittest.TestCase):
    def test_short_paragraphs_share_a_chunk(self):
        self.assertEqual(pack_paragraphs(["a.", "b.", "c."]), ["a.\nb.\nc."])

    def test_blank_paragraphs_are_dropped(self):
        self.assertEqual(pack_paragraphs(["a.", "   ", ""]), ["a."])
        self.assertEqual(pack_paragraphs([]), [])

    def test_never_cuts_a_paragraph_that_fits(self):
        paras = ["x" * 40, "y" * 40, "z" * 40]
        chunks = pack_paragraphs(paras, limit=90)
        self.assertEqual(chunks, ["x" * 40 + "\n" + "y" * 40, "z" * 40])

    def test_oversized_paragraph_splits_at_a_semicolon(self):
        text = "clause one is here; clause two is here; clause three"
        chunks = pack_paragraphs([text], limit=30)
        self.assertEqual(chunks[0], "clause one is here;")
        self.assertTrue(all(len(c) <= 30 for c in chunks))
        self.assertEqual(" ".join(chunks), text)

    def test_oversized_paragraph_flushes_what_came_before(self):
        chunks = pack_paragraphs(["short.", "w " * 40], limit=30)
        self.assertEqual(chunks[0], "short.")

    def test_a_paragraph_with_no_break_points_still_respects_the_limit(self):
        chunks = pack_paragraphs(["x" * 100], limit=30)
        self.assertTrue(all(len(c) <= 30 for c in chunks), [len(c) for c in chunks])
        self.assertEqual("".join(chunks), "x" * 100)

    def test_real_definitions_section_chunks_whole_paragraphs(self):
        # § 27-2004 (HMC definitions) is the largest section in the fixtures.
        section = _real("0-0-0-60027", "27-2004")
        chunks = section.chunks()
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), MAX_CHUNK_CHARS)
        for para in section.paragraphs:
            if len(para) <= MAX_CHUNK_CHARS:
                holding = [c for c in chunks if para in c.split("\n")]
                self.assertEqual(len(holding), 1, para[:60])

    def test_real_heat_subdivision_survives_intact_for_the_citation_guard(self):
        quote = "(2) between the hours of ten p.m. and six a.m., a temperature of at least sixty-two degrees Fahrenheit."
        chunks = _real("0-0-0-60027", "27-2029").chunks()
        self.assertTrue(any(quote in chunk for chunk in chunks))


class LatestEffectiveDateTests(unittest.TestCase):
    def test_reads_the_effective_date(self):
        self.assertEqual(latest_effective_date(["(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)"]), "2017-10-01")

    def test_takes_the_latest_of_several_amendments(self):
        # Real history line from § 26-1301.
        line = "(L.L. 2017/136, 8/11/2017, eff. 8/11/2017; Am. L.L. 2023/020, 2/19/2023, eff. 8/18/2023)"
        self.assertEqual(latest_effective_date([line]), "2023-08-18")

    def test_state_session_law_history(self):
        # Real history line from § 26-504.1 (repealed by the HSTPA).
        line = "(Repealed 2019 N.Y. Laws Ch. 36 Pt. D § 5, 6/14/2019, eff. 6/14/2019)"
        self.assertEqual(latest_effective_date([line]), "2019-06-14")

    def test_falls_back_to_enactment_date_without_eff(self):
        self.assertEqual(latest_effective_date(["(Am. L.L. 1991/039, 6/18/1991)"]), "1991-06-18")

    def test_none_when_no_date_is_named(self):
        self.assertIsNone(latest_effective_date([]))
        self.assertIsNone(latest_effective_date(["(Added by charter revision)"]))

    def test_property_reads_history(self):
        self.assertEqual(_real("0-0-0-60027", "27-2029").last_amended, "2017-10-01")


class PayloadTests(unittest.TestCase):
    def test_payload_carries_everything_the_rpc_needs(self):
        section = _real("0-0-0-60027", "27-2029")
        payload = section.as_payload()
        self.assertEqual(payload["section_key"], "test:27-2029")
        self.assertEqual(payload["content_hash"], section.content_hash)
        self.assertEqual(payload["last_amended"], "2017-10-01")
        self.assertEqual(payload["history"], ["(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)"])
        self.assertFalse(payload["repealed"])
        self.assertEqual([c["ordinal"] for c in payload["chunks"]], list(range(len(payload["chunks"]))))
        self.assertEqual(payload["full_text"], section.text)

    def test_repealed_section_has_no_chunks(self):
        payload = _real("0-0-0-60027", "27-2018").as_payload()
        self.assertTrue(payload["repealed"])
        self.assertEqual(payload["chunks"], [])
        self.assertEqual(payload["full_text"], "")


if __name__ == "__main__":
    unittest.main()
