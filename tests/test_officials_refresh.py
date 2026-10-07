"""tools/officials/refresh.py: keeping the street lookup's officials current.

Fixtures are real: the City Council members whose terms run past
2026-10-07 (NYC Open Data, 50 of 51 -- district 3 is vacant) and the
NY Senate API's 2025-session member lists (incumbents and former members).
"""

import io
import json
import os
import unittest
from datetime import date

from tools.officials import refresh

_FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "officials")


def _load(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as handle:
        return json.load(handle)


COUNCIL = _load("council_members_current.json")
SENATE = _load("nys_members_2025_senate.json")
ASSEMBLY = _load("nys_members_2025_assembly.json")
KEY = "k" * 40
TOKEN = "t" * 48


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, *_):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    def __init__(self, council=COUNCIL, senate=SENATE, assembly=ASSEMBLY, changes=None):
        self.council, self.senate, self.assembly = council, senate, assembly
        self.changes = changes if changes is not None else {}
        self.urls, self.rpcs = [], []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        if "uvw5-9znb" in url:
            return Response(self.council)
        if "/members/2025/senate" in url:
            return Response(self.senate)
        if "/members/2025/assembly" in url:
            return Response(self.assembly)
        if "/rpc/officials_replace" in url:
            body = json.loads(request.data)
            self.rpcs.append(body)
            return Response(self.changes.get(body["p_office"], []))
        raise AssertionError(url)


ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "anon", "CORPUS_INGEST_TOKEN": TOKEN, "NYSENATE_API_KEY": KEY}


def run(args, net, env=ENV, today=date(2026, 12, 15)):
    out = io.StringIO()
    code = refresh.main(args, env=dict(env), opener=net, out=out, today=today)
    return code, out.getvalue()


class FetchTests(unittest.TestCase):
    def test_council_names_are_tidied(self):
        rows = refresh.fetch_council(date(2026, 10, 7), Net())
        by = {r["district"]: r["name"] for r in rows}
        self.assertEqual(by[15], "Oswald J. Feliz")
        self.assertEqual(by[16], "Althea V. Stevens")  # double space in the source
        self.assertNotIn(3, by)

    def test_only_incumbents_and_one_per_district(self):
        senate = refresh.fetch_state("senate", KEY, date(2026, 10, 7), Net())
        self.assertEqual(len(senate), 62)
        self.assertEqual(len({r["district"] for r in senate}), 62)
        assembly = refresh.fetch_state("assembly", KEY, date(2026, 10, 7), Net())
        self.assertEqual(len(assembly), 150)
        self.assertEqual({r["district"]: r["name"] for r in assembly}[86], "Yudelka Tapia")

    def test_session_year(self):
        self.assertEqual(refresh.session_year(date(2026, 12, 15)), 2025)
        self.assertEqual(refresh.session_year(date(2027, 1, 15)), 2027)

    def test_the_key_never_appears_in_errors(self):
        def broken(request, timeout=None):
            raise OSError(f"connection reset for {request.full_url}")

        with self.assertRaises(refresh.RefreshError) as ctx:
            refresh.fetch_state("senate", KEY, date(2026, 10, 7), broken)
        self.assertNotIn(KEY, str(ctx.exception))


class RunTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        net = Net()
        code, out = run(["--dry-run"], net, env={"NYSENATE_API_KEY": KEY})
        self.assertEqual(code, 0, out)
        self.assertEqual(net.rpcs, [])
        self.assertIn("| City Council | 50 of 51 |", out)
        self.assertIn("| State Senate | 62 of 63 |", out)

    def test_write_sends_each_office_with_the_token(self):
        net = Net(changes={"council": [{"district": 3, "change": "removed", "old": "Erik Bottcher"}]})
        code, out = run([], net)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["p_office"] for r in net.rpcs], ["council", "assembly", "state_senate"])
        self.assertTrue(all(r["p_token"] == TOKEN for r in net.rpcs))
        self.assertIn("District 3: Erik Bottcher removed", out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(KEY, out)
        self.assertTrue(all(KEY not in u for u in net.urls if "supabase" in u))

    def test_a_half_empty_feed_is_refused_and_nothing_is_written(self):
        net = Net(council=COUNCIL[:20])
        code, out = run([], net)
        self.assertEqual(code, 3)
        self.assertEqual(net.rpcs, [])
        self.assertIn("council: 20 districts, minimum is 45", out)

    def test_two_people_for_one_district_is_refused(self):
        council = COUNCIL + [{"district": "15", "name": "Somebody Else"}]
        code, out = run([], Net(council=council))
        self.assertEqual(code, 3)
        self.assertIn("more than one person for district(s) [15]", out)

    def test_without_the_state_key_only_the_council_runs(self):
        env = {k: v for k, v in ENV.items() if k != "NYSENATE_API_KEY"}
        net = Net()
        code, out = run([], net, env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["p_office"] for r in net.rpcs], ["council"])
        self.assertIn("Skipped (NYSENATE_API_KEY not set)", out)

    def test_writing_needs_credentials(self):
        code, out = run([], Net(), env={"NYSENATE_API_KEY": KEY})
        self.assertEqual(code, 2)

    def test_changed_flag_for_the_workflow(self):
        import tempfile
        with tempfile.NamedTemporaryFile("r", delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)
        net = Net(changes={"assembly": [{"district": 74, "change": "changed", "old": "A", "name": "B"}]})
        code, _ = run([], net, env=dict(ENV, GITHUB_OUTPUT=path))
        self.assertEqual(code, 0)
        with open(path) as handle:
            self.assertIn("changed=true", handle.read())


class MigrationTests(unittest.TestCase):
    def test_writes_are_token_checked_and_reads_are_public(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "supabase", "migrations", "20261007_elected_officials.sql"), encoding="utf-8") as handle:
            sql = handle.read()
        self.assertIn("PERFORM public.corpus_check_token(p_token);", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.elected_officials FROM anon, authenticated;", sql)
        self.assertIn("SET search_path = public, extensions, pg_temp", sql)


if __name__ == "__main__":
    unittest.main()
