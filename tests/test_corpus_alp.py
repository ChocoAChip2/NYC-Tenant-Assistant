"""Tests for tools/corpus/alp.py against REAL American Legal Publishing XML.

tests/fixtures/alp/ holds unmodified excerpts of the official NYC Admin
Code bulk export (see its README). Nothing here is hand-written markup:
where a test needs a malformed or unusual file, it takes a real fixture
and changes one thing, so the rest of the document is still exactly what
ALP publishes.

The requirements come from docs/HANDOFF.md "Next steps" item 1.
"""

import io
import os
import unittest
import zipfile
import xml.etree.ElementTree as ET

from tools.corpus.alp import AlpParseError, parse_chapter, read_chapter_from_zip

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alp")

HMC = "0-0-0-60027"
RSL = "0-0-0-201924"
UE = "0-0-0-47504"
RTC = "0-0-0-47826"
HRL = "0-0-0-4607"

EXPECTED_CITATIONS = {
    HMC: ["27-2001", "27-2004", "27-2005", "27-2013", "27-2018", "27-2029", "27-2031"],
    RSL: ["26-501", "26-504.1", "26-517.1"],
    UE: [f"26-52{n}" for n in range(1, 10)],
    RTC: [f"26-130{n}" for n in range(1, 7)],
    HRL: ["8-101", "8-103", "8-104"],
}


def _raw(file_id: str) -> bytes:
    with open(os.path.join(FIXTURES, f"{file_id}.xml"), "rb") as handle:
        return handle.read()


def _parse(file_id: str, source_key: str = "src"):
    return parse_chapter(_raw(file_id), source_key=source_key, authority="NYC Admin Code")


def _by_citation(file_id: str):
    return {s.citation: s for s in _parse(file_id).sections}


def _all_sections():
    for file_id in EXPECTED_CITATIONS:
        yield from _parse(file_id, source_key=file_id).sections


class FixtureCoverageTests(unittest.TestCase):
    def test_every_kept_section_is_found_in_order(self):
        for file_id, citations in EXPECTED_CITATIONS.items():
            with self.subTest(file_id=file_id):
                self.assertEqual([s.citation for s in _parse(file_id).sections], citations)

    def test_real_fixtures_parse_without_warnings(self):
        # A warning means markup the parser does not understand. The full
        # zip may have some; these excerpts were chosen to have none.
        for file_id in EXPECTED_CITATIONS:
            with self.subTest(file_id=file_id):
                self.assertEqual(_parse(file_id).warnings, [])


class HeatSectionTests(unittest.TestCase):
    """§ 27-2029: the section a stale mirror gets wrong."""

    def setUp(self):
        self.section = _by_citation(HMC)["27-2029"]

    def test_current_overnight_rule(self):
        self.assertIn("at least sixty-two degrees Fahrenheit", self.section.text)
        self.assertNotIn("fifty-five degrees Fahrenheit", self.section.text)

    def test_no_publisher_tags(self):
        self.assertNotIn("[ALP S-", self.section.text)
        self.assertNotIn("[ALP", "".join(self.section.history))

    def test_history_and_last_amended(self):
        self.assertEqual(self.section.history, ["(Am. L.L. 2017/086, 5/30/2017, eff. 10/1/2017)"])
        self.assertEqual(self.section.last_amended, "2017-10-01")
        self.assertNotIn("L.L. 2017/086", self.section.text)

    def test_heading_title_path_and_url(self):
        self.assertEqual(self.section.title, "Minimum temperature to be maintained")
        self.assertEqual(
            self.section.heading_path,
            "Chapter 2: Housing Maintenance Code > Subchapter 2: Maintenance, Services, and Utilities"
            " > Article 8: Heat, Cooling, and Hot Water",
        )
        self.assertEqual(
            self.section.official_url,
            "https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-60410",
        )
        self.assertFalse(self.section.repealed)

    def test_paragraphs_are_the_subdivisions_verbatim(self):
        self.assertEqual(
            self.section.paragraphs[:3],
            [
                "a. During the period from October first through May thirty-first, centrally-supplied heat,"
                " in any dwelling in which such heat is required to be provided, shall be furnished so as to"
                " maintain, in every portion of such dwelling used or occupied for living purposes:",
                "(1) between the hours of six a.m. and ten p.m., a temperature of at least sixty-eight degrees"
                " Fahrenheit whenever the outside temperature falls below fifty-five degrees; and",
                "(2) between the hours of ten p.m. and six a.m., a temperature of at least sixty-two degrees"
                " Fahrenheit.",
            ],
        )
        self.assertEqual(len(self.section.paragraphs), 4)


class TextExtractionTests(unittest.TestCase):
    def test_no_publisher_tags_anywhere(self):
        for section in _all_sections():
            for text in [section.text, *section.history, *section.notes]:
                self.assertNotIn("[ALP", text, section.section_key)

    def test_heading_echo_is_not_law_text(self):
        for section in _all_sections():
            self.assertFalse(section.text.startswith("§"), section.section_key)
        self.assertEqual(
            _by_citation(HMC)["27-2001"].paragraphs,
            ['This chapter shall be known and may be cited as the "housing maintenance code."'],
        )

    def test_tabs_become_single_spaces(self):
        first = _by_citation(UE)["26-521"].paragraphs[0]
        self.assertTrue(first.startswith("a. It shall be unlawful for any person to evict"), first[:60])
        for section in _all_sections():
            self.assertNotIn("  ", section.text, section.section_key)
            self.assertNotIn("\t", section.text, section.section_key)

    def test_cross_reference_links_keep_their_text(self):
        text = _by_citation(UE)["26-522"].text
        self.assertIn('"Dwelling unit" means a dwelling unit as such term is defined in subdivision thirteen'
                      " of section 27-2004 of the housing maintenance code.", text)

    def test_bold_defined_terms_keep_their_text(self):
        self.assertIn('Coordinator. The term "coordinator" means the coordinator of the office of civil justice.',
                      _by_citation(RTC)["26-1301"].paragraphs)

    def test_non_ascii_punctuation_is_preserved(self):
        # § 27-2004 uses curly quotes in some definitions; they are the
        # publisher's characters and a verbatim library keeps them.
        self.assertIn("“unoccupied dwelling unit”", _by_citation(HMC)["27-2004"].text)


class EditorsNoteTests(unittest.TestCase):
    def test_notes_are_kept_apart_from_the_law(self):
        for citation, file_id in [("27-2004", HMC), ("27-2013", HMC), ("26-517.1", RSL), ("8-101", HRL)]:
            with self.subTest(citation=citation):
                section = _by_citation(file_id)[citation]
                self.assertEqual(len(section.notes), 1)
                self.assertTrue(section.notes[0].startswith("Editor's note:"), section.notes[0])
                self.assertNotIn("Editor's note", section.text)

    def test_note_does_not_change_the_hash(self):
        section = _by_citation(HMC)["27-2013"]
        bare_hash = section.content_hash
        section.notes = []
        self.assertEqual(section.content_hash, bare_hash)

    def test_no_editors_note_leaks_into_any_law_text(self):
        for section in _all_sections():
            self.assertNotIn("Editor's note", section.text, section.section_key)


class RepealedTests(unittest.TestCase):
    REPEALED = [(HMC, "27-2018"), (RSL, "26-504.1"), (HRL, "8-103"), (HRL, "8-104")]

    def test_repealed_sections_are_flagged(self):
        for file_id, citation in self.REPEALED:
            with self.subTest(citation=citation):
                section = _by_citation(file_id)[citation]
                self.assertTrue(section.repealed)
                self.assertEqual(section.paragraphs, [])
                self.assertTrue(section.history[-1].startswith("(Repealed"))
                self.assertNotIn("Repealed", section.title)

    def test_repeal_comes_from_the_heading_marker_not_the_history(self):
        # "(Repealed and added L.L. ...)" is an active section, so history
        # alone never marks a repeal; an empty unmarked section is reported.
        data = _raw(HMC).replace(b"mandatory extermination. [Repealed]<", b"mandatory extermination.<")
        result = parse_chapter(data, source_key="src", authority="x")
        section = {s.citation: s for s in result.sections}["27-2018"]
        self.assertFalse(section.repealed)
        self.assertEqual(result.warnings, ["§ 27-2018: no law text and not marked repealed"])

    def test_repeal_dates(self):
        self.assertEqual(_by_citation(HMC)["27-2018"].last_amended, "2019-01-19")
        self.assertEqual(_by_citation(RSL)["26-504.1"].last_amended, "2019-06-14")

    def test_everything_else_is_active_and_has_text(self):
        repealed = {c for _, c in self.REPEALED}
        for section in _all_sections():
            if section.citation not in repealed:
                self.assertFalse(section.repealed, section.section_key)
                self.assertTrue(section.paragraphs, section.section_key)


class HistoryLineTests(unittest.TestCase):
    def test_every_real_history_form_is_recognised(self):
        cases = {
            (RTC, "26-1301"): "(L.L. 2017/136, 8/11/2017, eff. 8/11/2017; Am. L.L. 2023/020, 2/19/2023, eff. 8/18/2023)",
            (RSL, "26-517.1"): "(Am. 2019 N.Y. Laws Ch. 36 Pt. K § 15, 6/14/2019, eff. 6/14/2019)",
            (RSL, "26-504.1"): "(Repealed 2019 N.Y. Laws Ch. 36 Pt. D § 5, 6/14/2019, eff. 6/14/2019)",
            (HMC, "27-2018"): "(Repealed L.L. 2018/055, 1/19/2018, eff. 1/19/2019)",
        }
        for (file_id, citation), line in cases.items():
            with self.subTest(citation=citation):
                section = _by_citation(file_id)[citation]
                self.assertEqual(section.history, [line])
                self.assertNotIn(line, section.text)

    def test_subdivisions_in_parentheses_are_not_history(self):
        paragraphs = _by_citation(RSL)["26-517.1"].paragraphs
        self.assertTrue(any(p.startswith("(2) If such payment is not made") for p in paragraphs))

    def test_sections_without_history_report_no_date(self):
        section = _by_citation(UE)["26-521"]
        self.assertEqual(section.history, [])
        self.assertIsNone(section.last_amended)


class RightToCounselTests(unittest.TestCase):
    """Chapter 13's § 26-1301, one of two § 26-1301s in Title 26."""

    def setUp(self):
        self.result = parse_chapter(_raw(RTC), source_key="nyc-rtc", authority="NYC Admin Code")
        self.section = self.result.sections[0]

    def test_asterisk_is_stripped_from_title_and_path(self):
        self.assertEqual(self.section.citation, "26-1301")
        self.assertEqual(self.section.title, "Definitions")
        self.assertEqual(self.section.heading_path, "Chapter 13: Provision of Legal Services in Eviction Proceedings")

    def test_duplicate_numbering_note_is_a_note(self):
        self.assertEqual(self.section.notes, ["* Editor's note: there are two sections designated as § 26-1301."])
        self.assertTrue(self.section.paragraphs[0].startswith("For the purposes of this chapter"))

    def test_key_is_scoped_to_the_source(self):
        self.assertEqual(self.section.section_key, "nyc-rtc:26-1301")


class SectionKeyTests(unittest.TestCase):
    def test_section_keys_are_unique_across_all_fixtures(self):
        keys = [s.section_key for s in _all_sections()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_a_duplicate_within_one_chapter_is_refused(self):
        # A real section, repeated: a parse that would silently let one
        # overwrite the other must fail loudly instead.
        root = ET.fromstring(_raw(UE))
        parent = next(p for p in root.iter("LEVEL") if any(c.get("style-name") == "Section" for c in p))
        first = next(c for c in parent if c.get("style-name") == "Section")
        parent.append(first)
        with self.assertRaisesRegex(AlpParseError, "duplicate section_key src:26-521"):
            parse_chapter(ET.tostring(root), source_key="src", authority="NYC Admin Code")


class RobustnessTests(unittest.TestCase):
    def test_malformed_xml_is_refused(self):
        with self.assertRaises(AlpParseError):
            parse_chapter(_raw(UE)[:-200], source_key="src", authority="x")

    def test_entity_declarations_are_refused(self):
        data = _raw(UE).replace(
            b"<DOCUMENT", b'<!DOCTYPE DOCUMENT [<!ENTITY x "boom">]>\n<DOCUMENT', 1
        )
        with self.assertRaisesRegex(AlpParseError, "DOCTYPE"):
            parse_chapter(data, source_key="src", authority="x")

    def test_unknown_inline_markup_is_kept_and_reported(self):
        data = _raw(UE).replace(b"It shall be unlawful for any person", b"<NEWTHING>It shall</NEWTHING> be unlawful for any person", 1)
        result = parse_chapter(data, source_key="src", authority="x")
        self.assertIn("It shall be unlawful for any person", result.sections[0].text)
        self.assertEqual(result.warnings, ["§ 26-521: unknown inline element <NEWTHING>, text kept"])

    def test_unknown_paragraph_style_is_kept_and_reported(self):
        root = ET.fromstring(_raw(UE))
        body = next(r for r in root.iter("RECORD") if r.get("id") == "0-0-0-47506")
        body.find("PARA").set("style-name", "Table")
        result = parse_chapter(ET.tostring(root), source_key="src", authority="x")
        self.assertTrue(result.sections[0].text.startswith("a. It shall be unlawful"))
        self.assertIn("§ 26-521: unknown PARA style 'Table', kept as law text", result.warnings)

    def test_unrecognised_section_heading_is_skipped_and_reported(self):
        data = _raw(UE).replace(b"<HEADING>\xc2\xa7 26-529 ", b"<HEADING>Appendix ", 1)
        result = parse_chapter(data, source_key="src", authority="x")
        self.assertNotIn("26-529", [s.citation for s in result.sections])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("unrecognised section heading 'Appendix", result.warnings[0])

    def test_heading_echo_is_skipped_even_when_it_differs(self):
        data = _raw(HMC).replace(b"mandatory extermination. [Repealed]</HEADING>", b"mandatory extermination.</HEADING>", 1)
        result = parse_chapter(data, source_key="src", authority="x")
        section = {s.citation: s for s in result.sections}["27-2018"]
        self.assertEqual(section.paragraphs, [])
        self.assertTrue(any("heading echo differs" in w for w in result.warnings), result.warnings)

    def test_history_lookalike_is_reported(self):
        # A paragraph with a local-law cite in parentheses that does not
        # open like a history line: kept as law, but flagged for a human.
        root = ET.fromstring(_raw(UE))
        record = next(r for r in root.iter("RECORD") if r.get("id") == "0-0-0-47510")
        para = record.find("PARA")
        for child in list(para):
            para.remove(child)
        para.text = "(as added by L.L. 1982/056)"
        result = parse_chapter(ET.tostring(root), source_key="src", authority="x")
        self.assertIn("(as added by L.L. 1982/056)", result.sections[0].paragraphs)
        self.assertTrue(any("looks like history" in w for w in result.warnings))


class ZipTests(unittest.TestCase):
    def _zip(self, names):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, _raw(UE))
        return buffer.getvalue()

    def test_reads_a_chapter_by_file_id(self):
        data = self._zip(["XML/0-0-0-47504.xml", "XML/0-0-0-4607.xml"])
        self.assertEqual(read_chapter_from_zip(data, UE), _raw(UE))

    def test_does_not_match_on_a_suffix_of_another_id(self):
        # "0-0-0-4607" must not match "XML/0-0-0-14607.xml".
        data = self._zip(["XML/0-0-0-14607.xml"])
        with self.assertRaisesRegex(AlpParseError, "found 0"):
            read_chapter_from_zip(data, HRL)

    def test_reads_from_a_path(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "XML.zip")
            with open(path, "wb") as handle:
                handle.write(self._zip(["XML/0-0-0-47504.xml"]))
            self.assertEqual(read_chapter_from_zip(path, UE), _raw(UE))


if __name__ == "__main__":
    unittest.main()


class FullZipShapesTests(unittest.TestCase):
    """Markup the fixtures never showed but the full zip did (2026-09-28).

    Each test takes a real fixture and changes it into the exact shape the
    full-zip dry run reported, quoted in the test.
    """

    def _section_level(self, root, citation):
        for level in root.iter("LEVEL"):
            if level.get("style-name") != "Section":
                continue
            heading = level.find("RECORD/HEADING")
            if heading is not None and "".join(heading.itertext()).strip().startswith(f"\u00a7 {citation} "):
                return level
        raise AssertionError(citation)

    def test_space_after_the_hyphen_in_a_heading(self):
        # Real: "§ 27- 2017.4. Violation for pests" (record 0-0-0-60278) and
        # "§ 27- 2017.8 Integrated pest management practices." (0-0-0-60305).
        # Both the HEADING and its echo carry the space.
        data = _raw(UE).replace("\u00a7 26-529 ".encode(), "\u00a7 26- 529 ".encode())
        result = parse_chapter(data, source_key="src", authority="x")
        self.assertEqual([s.citation for s in result.sections][-1], "26-529")
        self.assertEqual(result.warnings, [])

    def test_space_after_the_hyphen_with_a_trailing_period(self):
        data = _raw(UE).replace("\u00a7 26-529 ".encode(), "\u00a7 26- 529. ".encode())
        result = parse_chapter(data, source_key="src", authority="x")
        section = result.sections[-1]
        self.assertEqual((section.citation, section.title), ("26-529", "Remedies and penalties"))

    def test_large_editors_note_style_is_a_note(self):
        # Real (§ 27-2093.1): <PARA style-name="EdNote">Editor's note: this
        # section has been amended by L.L. 2026/138, 9/12/2026, eff. 4/15/2027.
        # It is publisher commentary about a future change, not law.
        root = ET.fromstring(_raw(UE))
        body = next(r for r in root.iter("RECORD") if r.get("id") == "0-0-0-47506")
        body.find("PARA").set("style-name", "EdNote")
        result = parse_chapter(ET.tostring(root), source_key="src", authority="x")
        first = result.sections[0]
        self.assertFalse(first.text.startswith("a. It shall be unlawful"))
        self.assertTrue(any(n.startswith("a. It shall be unlawful") for n in first.notes))
        self.assertEqual(result.warnings, [])

    def _make_reserved(self, keep_body):
        root = ET.fromstring(_raw(UE))
        level = self._section_level(root, "26-529")
        record = level.find("RECORD")
        record.find("HEADING").text = "\u00a7 26-529 Reserved."
        echo = record.find("PARA")
        for child in list(echo):
            echo.remove(child)
        echo.text = "\u00a7 26-529 Reserved."
        if not keep_body:
            # Real (§§ 8-108, 8-110): the body is one EdNoteSm pointing at
            # Appendix A, and no law text.
            for body in level.findall("LEVEL"):
                for rec in body.findall("RECORD"):
                    body.remove(rec)
                note = ET.SubElement(ET.SubElement(body, "RECORD"), "PARA", {"style-name": "EdNoteSm"})
                note.text = "Editor's note: For related unconsolidated provisions, see Appendix A."
        return parse_chapter(ET.tostring(root), source_key="src", authority="x")

    def test_reserved_placeholder_is_left_out_quietly(self):
        result = self._make_reserved(keep_body=False)
        self.assertNotIn("26-529", [s.citation for s in result.sections])
        self.assertEqual(len(result.sections), 8)
        self.assertEqual(result.warnings, [])

    def test_reserved_title_with_law_text_is_kept_and_reported(self):
        result = self._make_reserved(keep_body=True)
        self.assertIn("26-529", [s.citation for s in result.sections])
        self.assertEqual(result.warnings, ["\u00a7 26-529: titled Reserved but has law text, kept"])
