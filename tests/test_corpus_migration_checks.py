"""The migration's live-DB verification script must match today's client.

supabase/checks/20260929_legal_library_checks.sql is run (rolled back)
against the live project before the migration is applied. It embeds real
parser output and pinned hashes; these tests fail if either has drifted
from what tools/corpus produces now.
"""

import json
import os
import re
import unittest

from tools.corpus.alp import parse_chapter
from tools.corpus.model import law_hash

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alp")


class MigrationChecksFileTests(unittest.TestCase):
    """supabase/checks/20260929_legal_library_checks.sql embeds parser output.

    That file is what proves the migration against the live database, so
    its payloads and pinned hashes must be what tools/corpus produces
    today -- otherwise it would prove the server agrees with an old client.
    """

    PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "supabase", "checks", "20260929_legal_library_checks.sql")

    @classmethod
    def setUpClass(cls):
        with open(cls.PATH, encoding="utf-8") as handle:
            cls.sql = handle.read()

    def _json(self, tag):
        return json.loads(re.search(rf"\${tag}\$(.*?)\${tag}\$", self.sql, re.S).group(1))

    def _payloads(self, file_id, key):
        with open(os.path.join(FIXTURES, f"{file_id}.xml"), "rb") as handle:
            return [s.as_payload() for s in parse_chapter(handle.read(), source_key=key,
                                                          authority="NYC Admin Code").sections]

    def test_ue_payload_is_current_parser_output(self):
        self.assertEqual(self._json("ue_json"), self._payloads("0-0-0-47504", "nyc-ue"))

    def test_heat_payload_is_current_parser_output(self):
        heat = [p for p in self._payloads("0-0-0-60027", "nyc-hmc") if p["citation"] == "27-2029"]
        self.assertEqual(self._json("heat_json"), heat)

    def test_amended_payload_hash_is_consistent(self):
        (amended,) = self._json("amended_json")
        self.assertEqual(amended["content_hash"], law_hash(amended["full_text"]))
        self.assertNotIn("sixty-two", amended["full_text"])

    def test_pinned_hashes_match_law_hash(self):
        self.assertIn(f"= '{law_hash('a b')}', 'ASCII whitespace", self.sql)
        self.assertIn(f"= '{law_hash('a' + chr(160) + 'b')}',", self.sql)


if __name__ == "__main__":
    unittest.main()
