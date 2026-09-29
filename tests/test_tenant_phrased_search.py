"""Tests for the tenant-phrased legal search (20260930 migration) and its eval tool.

The search itself is SQL, and this suite never touches a database or the
network, so these tests pin down what can be checked statically: the
migration keeps search_legal_documents' contract with the app, the synonym
data is well formed, and tools/corpus/eval_search.py scores correctly.
The behaviour was measured against a local copy of the real 308-section
library and against the live project (log/2026-09-30-tenant-phrased-search.txt).
"""

import io
import json
import os
import re
import unittest

from tools.corpus import eval_search

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(ROOT, "supabase", "migrations", "20260930_tenant_phrased_search.sql")
LIBRARY = os.path.join(ROOT, "supabase", "migrations", "20260929_legal_library.sql")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "tenant_questions.json")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _function(sql, name):
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return sql[start:sql.index("$$;", start)]


def _returns(block):
    return re.search(r"RETURNS TABLE \((.*?)\)\s*LANGUAGE", block, re.S).group(1).split()


class MigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.sql = _read(MIGRATION)

    def test_search_keeps_the_signature_and_columns_the_app_reads(self):
        new = _function(self.sql, "search_legal_documents")
        old = _function(_read(LIBRARY), "search_legal_documents")
        self.assertEqual(new[:new.index("RETURNS")], old[:old.index("RETURNS")])
        self.assertEqual(_returns(new), _returns(old))

    def test_search_still_filters_to_active_sections_and_runs_as_caller(self):
        block = _function(self.sql, "search_legal_documents")
        self.assertIn("s.status = 'active'", block)
        self.assertIn("SECURITY INVOKER", block)
        self.assertIn("SET search_path = public, extensions, pg_temp", block)

    def test_group_helper_is_invoker_with_a_pinned_search_path(self):
        block = _function(self.sql, "legal_query_groups")
        self.assertIn("SECURITY INVOKER", block)
        self.assertIn("SET search_path = public, pg_temp", block)

    def test_query_words_are_quoted_never_spliced_into_tsquery(self):
        # Tenant text reaches to_tsquery only as quoted lexemes or through
        # phraseto_tsquery, so "a & b | !c" is words, not operators.
        block = _function(self.sql, "legal_query_groups")
        self.assertIn("quote_literal(s.lx)", block)
        self.assertIn("phraseto_tsquery('english', item)", block)

    def test_synonyms_are_public_read_and_not_writable_by_clients(self):
        self.assertIn("ALTER TABLE public.legal_search_synonyms ENABLE ROW LEVEL SECURITY", self.sql)
        self.assertIn("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.legal_search_synonyms FROM anon, authenticated",
                      self.sql)

    def test_synonym_rows_are_well_formed(self):
        block = self.sql[self.sql.index("INSERT INTO public.legal_search_synonyms"):self.sql.index("ON CONFLICT")]
        rows = re.findall(r"\('([^']*)'\s*,\s*'([^']*)'\)", block)
        self.assertGreater(len(rows), 50)
        words = [w for w, _ in rows]
        self.assertEqual(len(words), len(set(words)), "duplicate tenant_word")
        for word, law in rows:
            with self.subTest(word=word):
                self.assertEqual(word, word.strip().lower())
                self.assertTrue(all(item.strip() for item in law.split(",")), law)

    def test_the_tenant_words_that_failed_before_are_mapped(self):
        block = self.sql[self.sql.index("INSERT INTO public.legal_search_synonyms"):]
        for word, law in [("landlord", "owner"), ("lawyer", "legal services"), ("counsel", "legal services"),
                          ("roaches", "cockroaches"), ("bugs", "bedbug"), ("voucher", "lawful source of income"),
                          ("electricity", "essential services")]:
            with self.subTest(word=word):
                self.assertRegex(block, rf"\('{word}'\s*,\s*'[^']*{law}")


class FixtureTests(unittest.TestCase):
    def test_fixture_shape(self):
        data = json.loads(_read(FIXTURE))
        self.assertGreaterEqual(len(data["tuning"]), 20)
        self.assertGreaterEqual(len(data["held_out"]), 15)
        for case in data["tuning"] + data["held_out"]:
            self.assertTrue(case["q"].strip())
            self.assertTrue(case["any_of"])
            for citation in case["any_of"]:
                self.assertRegex(citation, r"^\d+-\d+(\.\d+)*$")


class _Fake:
    def __init__(self, answers):
        self.answers = answers
        self.bodies = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data)
        self.bodies.append((request.full_url, dict(request.header_items()), body))
        rows = [{"citation": c} for c in self.answers.get(body["query_text"], [])]
        response = io.BytesIO(json.dumps(rows).encode())
        response.__enter__ = lambda *a: response
        response.__exit__ = lambda *a: None
        return response


class EvalToolTests(unittest.TestCase):
    ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "anon"}

    def _run(self, answers, argv=()):
        fake, out = _Fake(answers), io.StringIO()
        code = eval_search.main(list(argv), env=self.ENV, opener=fake, out=out)
        return code, out.getvalue(), fake

    def test_counts_a_hit_when_any_listed_section_is_returned(self):
        data = json.loads(_read(FIXTURE))
        answers = {c["q"]: ["99-9999", c["any_of"][-1]] for c in data["tuning"] + data["held_out"]}
        code, out, _ = self._run(answers, ["--min-recall", "1.0"])
        total = len(data["tuning"]) + len(data["held_out"])
        self.assertEqual(code, 0, out)
        self.assertIn(f"recall@6: {total}/{total} = 100%", out)

    def test_below_the_minimum_exits_1(self):
        code, out, _ = self._run({}, ["--min-recall", "0.5"])
        self.assertEqual(code, 1)
        self.assertIn("= 0%", out)

    def test_calls_the_apps_rpc_read_only_with_the_anon_key(self):
        _, _, fake = self._run({})
        url, headers, body = fake.bodies[0]
        self.assertEqual(url, "https://x.supabase.co/rest/v1/rpc/search_legal_documents")
        self.assertEqual(headers["Apikey"], "anon")
        self.assertEqual(set(body), {"query_embedding", "query_text", "match_count"})
        self.assertIsNone(body["query_embedding"])

    def test_needs_credentials(self):
        out = io.StringIO()
        self.assertEqual(eval_search.main([], env={}, opener=_Fake({}), out=out), 2)


if __name__ == "__main__":
    unittest.main()
