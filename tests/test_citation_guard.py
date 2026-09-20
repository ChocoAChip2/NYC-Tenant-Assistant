"""Tests for the citation guard.

The guard's whole reason for existing is the case in
CitationWithWrongNumberTests: a REAL section, correctly retrieved,
correctly cited, with a fabricated number attached to it. The first
design of this guard passed that reply. It is the first test here so
nobody removes the number check thinking it is redundant with the
statute check.

Every test builds its passages by hand rather than through Supabase --
the guard is pure text analysis and must stay testable without a database
or an API key.
"""

import unittest

import citation_guard as guard

HEAT = guard.Passage(
    marker="S1",
    citation="27-2029",
    authority="NYC Administrative Code",
    official_url="https://codelibrary.amlegal.com/example",
    text=(
        "Between the hours of 10:00 PM and 6:00 AM, an indoor temperature of at "
        "least 62 degrees Fahrenheit shall be maintained in every dwelling unit, "
        "regardless of the outdoor temperature."
    ),
)

DEPOSIT = guard.Passage(
    marker="S2",
    citation="7-108",
    authority="NY General Obligations Law",
    official_url="https://example.gov/gol-7-108",
    text=(
        "A landlord shall return any remaining portion of the deposit to the "
        "tenant within fourteen days after the tenant has vacated the premises."
    ),
)


class CitationWithWrongNumberTests(unittest.TestCase):
    """The failure this module was written for."""

    def test_a_fabricated_temperature_under_a_real_citation_is_caught(self):
        reply = "Under [S1], your landlord must keep your apartment at 70 degrees overnight."

        result = guard.check(reply, [HEAT])

        self.assertFalse(result.ok)
        self.assertIn("number_not_in_source", result.kinds)

    def test_the_correct_temperature_with_a_verbatim_quote_passes(self):
        reply = (
            'Overnight the rule is "an indoor temperature of at least 62 degrees '
            'Fahrenheit shall be maintained" [S1].'
        )

        result = guard.check(reply, [HEAT])

        self.assertTrue(result.ok, result.violations)

    def test_a_fabricated_deadline_is_caught(self):
        reply = 'Your landlord has "return any remaining portion of the deposit" [S2] within 30 days.'

        result = guard.check(reply, [HEAT, DEPOSIT])

        self.assertIn("number_not_in_source", result.kinds)

    def test_the_real_deadline_passes_even_though_it_is_spelled_out(self):
        """The source says "fourteen days"; a reply quoting it is fine.
        The number check only fires on digits the reply itself asserts."""
        reply = 'The deposit must be returned "within fourteen days after the tenant has vacated" [S2].'

        result = guard.check(reply, [HEAT, DEPOSIT])

        self.assertTrue(result.ok, result.violations)


class MarkerTests(unittest.TestCase):
    def test_a_marker_that_was_never_shown_is_caught(self):
        reply = 'The code says "shall be maintained" [S7].'

        result = guard.check(reply, [HEAT])

        self.assertIn("unknown_marker", result.kinds)

    def test_an_uncited_reply_is_fine(self):
        """No citation, no authority claimed, nothing to verify. This is the
        honest fallback when retrieval finds nothing, so it must not be
        treated as a violation."""
        reply = "Generally, landlords in NYC have to provide heat during the winter months."

        result = guard.check(reply, [HEAT])

        self.assertTrue(result.ok, result.violations)
        self.assertEqual(result.cited_markers, [])


class StatuteGroundingTests(unittest.TestCase):
    def test_an_invented_section_number_is_caught(self):
        reply = "Section 41-9987 requires your landlord to repaint every two years."

        result = guard.check(reply, [HEAT])

        self.assertIn("ungrounded_statute", result.kinds)

    def test_a_retrieved_section_number_is_accepted_in_any_spelling(self):
        for spelling in ("Section 27-2029", "§ 27-2029", "Admin. Code 27-2029"):
            with self.subTest(spelling=spelling):
                result = guard.check(f"See {spelling} for the overnight rule.", [HEAT])

                self.assertNotIn("ungrounded_statute", result.kinds)

    def test_a_statute_the_tenant_mentioned_first_is_not_held_against_the_reply(self):
        """A tenant pastes their lease or names a law; repeating it back is
        not a hallucination, and rejecting the reply would make the
        assistant unable to discuss what it was just asked about."""
        reply = "You mentioned Local Law 18, which I do not have the text of here."

        result = guard.check(reply, [HEAT], user_message="does Local Law 18 apply to me?")

        self.assertNotIn("ungrounded_statute", result.kinds)

    def test_agency_names_and_phone_numbers_are_not_statutes(self):
        reply = "File a complaint with HPD by calling 311, or reach the helpline at (212) 962-4795."

        result = guard.check(reply, [HEAT])

        self.assertTrue(result.ok, result.violations)


class QuoteAnchoringTests(unittest.TestCase):
    def test_a_citation_with_no_quote_at_all_is_caught(self):
        reply = "The code covers overnight heat [S1]."

        result = guard.check(reply, [HEAT])

        self.assertIn("citation_without_quote", result.kinds)

    def test_a_quote_that_is_not_in_the_cited_source_is_caught(self):
        reply = 'The law says "landlords must install a thermostat in every room" [S1].'

        result = guard.check(reply, [HEAT])

        self.assertIn("quote_not_in_source", result.kinds)

    def test_a_quote_from_the_wrong_retrieved_source_is_caught(self):
        """Quoting S2's text while citing S1 is still a misattribution,
        even though both were retrieved."""
        reply = 'Overnight, "within fourteen days after the tenant has vacated" [S1].'

        result = guard.check(reply, [HEAT, DEPOSIT])

        self.assertIn("quote_not_in_source", result.kinds)

    def test_curly_quotes_and_collapsed_whitespace_still_match(self):
        reply = (
            "“an indoor temperature of at least 62 degrees Fahrenheit\n"
            "shall be maintained” [S1]."
        )

        result = guard.check(reply, [HEAT])

        self.assertTrue(result.ok, result.violations)


class StripAndRenderTests(unittest.TestCase):
    def test_stripping_leaves_readable_prose(self):
        stripped = guard.strip_citations('The rule is "shall be maintained" [S1]. Call 311 [S2].')

        self.assertNotIn("[S1]", stripped)
        self.assertNotIn("[S2]", stripped)
        self.assertIn('The rule is "shall be maintained".', stripped)

    def test_chips_are_rendered_only_for_markers_the_reply_used(self):
        reply = 'See "shall be maintained" [S1].'
        result = guard.check(reply, [HEAT, DEPOSIT])

        chips = guard.render_sources([HEAT, DEPOSIT], result)

        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0]["label"], "NYC Administrative Code 27-2029")
        self.assertTrue(chips[0]["url"].startswith("https://"))

    def test_a_passage_without_a_url_produces_no_chip(self):
        """A citation a tenant cannot open is an assertion, not a source."""
        bare = guard.Passage(marker="S1", text=HEAT.text, citation="27-2029", authority="NYC")
        result = guard.check('See "shall be maintained" [S1].', [bare])

        self.assertEqual(guard.render_sources([bare], result), [])


class GuardModeTests(unittest.TestCase):
    def test_report_and_enforce_are_the_only_modes(self):
        self.assertEqual({mode.value for mode in guard.GuardMode}, {"report", "enforce"})


if __name__ == "__main__":
    unittest.main()


class QuoteFormattingTests(unittest.TestCase):
    """Every case here was a real false positive found by fuzzing the guard
    against the shapes a model actually writes.

    False positives matter as much as false negatives: in ENFORCE mode an
    unrecognised quote strips the citation off a CORRECT answer, and the
    failure looks like a dull model rather than a bug, so nobody
    investigates. These lock in the fix.
    """

    QUOTE = "at least 62 degrees Fahrenheit shall be maintained"

    def assertClean(self, reply):
        result = guard.check(reply, [HEAT])
        self.assertTrue(result.ok, [f"{v.kind}: {v.detail}" for v in result.violations])

    def test_markdown_blockquote_after_a_colon_lead_in(self):
        self.assertClean(f"According to [S1]:\n\n> an indoor temperature of {self.QUOTE}")

    def test_blockquote_before_the_citation(self):
        self.assertClean(f"> an indoor temperature of {self.QUOTE}\n\nThat is [S1].")

    def test_backtick_span(self):
        self.assertClean(f"The rule is `{self.QUOTE}` [S1].")

    def test_single_quotes(self):
        self.assertClean(f"The rule is '{self.QUOTE}' [S1].")

    def test_quotes_nested_inside_quotes(self):
        self.assertClean(f"He said \"she said '{self.QUOTE}'\" [S1].")

    def test_quote_in_the_previous_sentence(self):
        self.assertClean(f'The code is explicit. It says "{self.QUOTE}". See [S1].')

    def test_closing_period_inside_the_quotation_marks(self):
        """American style, and the single most common way this broke."""
        self.assertClean(f'The rule is "{self.QUOTE}." [S1]')

    def test_quote_broken_across_a_line(self):
        self.assertClean('The rule: "an indoor temperature of at least 62 degrees\nFahrenheit shall be maintained" [S1].')

    def test_markdown_bold_around_the_quote(self):
        self.assertClean(f'The rule is **"{self.QUOTE}"** [S1].')


class AdjacencyDoesNotLaunderFabricationTests(unittest.TestCase):
    """Letting a citation borrow a quote from the neighbouring line is what
    makes blockquotes work. It must not also let a fabricated claim ride
    along on a real quote sitting next to it."""

    def test_a_fabricated_number_beside_a_real_quote_is_still_caught(self):
        reply = (
            'The rule is "at least 62 degrees Fahrenheit shall be maintained" [S1]. '
            "It must be 75 degrees by law [S1]."
        )

        self.assertIn("number_not_in_source", guard.check(reply, [HEAT]).kinds)

    def test_a_fabrication_after_a_blockquote_is_still_caught(self):
        reply = (
            "> an indoor temperature of at least 62 degrees Fahrenheit shall be maintained\n\n"
            "So [S1] requires 80 degrees."
        )

        self.assertIn("number_not_in_source", guard.check(reply, [HEAT]).kinds)

    def test_a_quote_several_lines_away_does_not_rescue_the_citation(self):
        reply = (
            'The rule is "at least 62 degrees Fahrenheit shall be maintained".\n'
            "Filler line one.\nFiller line two.\nTherefore [S1] applies."
        )

        self.assertIn("citation_without_quote", guard.check(reply, [HEAT]).kinds)


class RobustnessTests(unittest.TestCase):
    """The guard runs on model output, which is untrusted in shape if not in
    intent. It must never raise and never blow up on length."""

    def test_pathological_inputs_do_not_raise(self):
        cases = {
            "empty reply": "",
            "no sentence enders": "a" * 5000 + " [S1] " + "b" * 5000,
            "two hundred markers": " ".join(f"[S{i}]" for i in range(200)),
            "control characters": "\x00﻿‮ quote “test” [S1] \U0001f600",
            "only whitespace": "   \n\n\t  ",
        }
        for name, reply in cases.items():
            with self.subTest(case=name):
                self.assertIsInstance(guard.check(reply, [HEAT]).ok, bool)

    def test_a_marker_with_no_passages_at_all_is_an_unknown_marker(self):
        self.assertIn("unknown_marker", guard.check("Reply [S1].", []).kinds)

    def test_a_long_reply_stays_fast_enough_to_run_inline(self):
        """No catastrophic backtracking: this runs on every chat turn."""
        import time

        reply = ('The law says "' + "x" * 200 + '" [S1]. ') * 400
        started = time.perf_counter()
        guard.check(reply, [HEAT])
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 2.0, f"guard took {elapsed:.2f}s on {len(reply)} chars")


class ChipUrlSafetyTests(unittest.TestCase):
    """Chip URLs come from a database row and will end up inside an href.
    The corpus is write-protected and the ingest validator demands https,
    so this is the third lock on the same door -- and the cheapest."""

    def _chip_count(self, url):
        passage = guard.Passage(
            marker="S1", text=HEAT.text, citation="27-2029", authority="NYC", official_url=url
        )
        result = guard.check('See "shall be maintained" [S1].', [passage])
        return len(guard.render_sources([passage], result))

    def test_https_urls_render(self):
        self.assertEqual(self._chip_count("https://example.gov/x"), 1)

    def test_dangerous_schemes_never_render(self):
        for url in (
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "  javascript:alert(1)  ",
            "//evil.example/x",
        ):
            with self.subTest(url=url):
                self.assertEqual(self._chip_count(url), 0)

    def test_plain_http_does_not_render_either(self):
        """Statute text fetched over http could have been tampered with in
        transit, which is the one thing a citation must not be."""
        self.assertEqual(self._chip_count("http://example.gov/x"), 0)
