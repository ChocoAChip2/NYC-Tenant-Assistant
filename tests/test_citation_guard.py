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
