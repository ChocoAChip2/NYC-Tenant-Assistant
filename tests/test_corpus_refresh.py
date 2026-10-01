"""Tests for tools/corpus/registry.py and tools/corpus/refresh.py.

The network is faked by URL: ALP's zip download and Supabase's PostgREST
endpoints. The zip itself is built from the real ALP fixtures, and the
Unlawful Eviction and Right to Counsel fixtures are COMPLETE chapters (all
9 and all 6 sections), so those two real registry entries pass the real
gates. The HMC fixture is a 7-section excerpt of a 211-section chapter,
so it fails them -- which is exactly the broken-parse case the gates exist
for.

What matters most: a failed gate writes nothing for any source, dry runs
never write, and a write uses the ingest-token RPCs and never anything else.
"""

import hashlib
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
import zipfile
from unittest import mock

from tools.corpus import refresh
from tools.corpus.alp import parse_chapter
from tools.corpus.model import law_hash
from tools.corpus.registry import ALP_ADMIN_ZIP_URL, ALP_SOURCES, NYS_SOURCES, AlpSource, all_sources, alp_sources

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "alp")
SUPABASE_URL = "https://example.supabase.co"
ENV = {"SUPABASE_URL": SUPABASE_URL, "SUPABASE_KEY": "anon-key", "CORPUS_INGEST_TOKEN": "t" * 64}


def _fixture_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(os.listdir(FIXTURES)):
            if name.endswith(".xml"):
                with open(os.path.join(FIXTURES, name), "rb") as handle:
                    archive.writestr(f"XML/{name}", handle.read())
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeNetwork:
    """ALP + a Supabase that keeps legal_sources in memory like the RPCs do."""

    def __init__(self, zip_bytes=None, rows=None, fail_rpc=None, failures=None, fail_after=None):
        self.zip_bytes = zip_bytes
        self.rows = rows if rows is not None else {}  # section_key -> {content_hash, status, source_key}
        self.fail_rpc = fail_rpc
        # name -> list of exceptions raised by successive calls, then success
        self.failures = {k: list(v) for k, v in (failures or {}).items()}
        # (name, n): the n-th call (1-based) of `name` and every later one fail with HTTP 500
        self.fail_after = fail_after
        self.counts = {}
        self.calls = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, url, body, dict(request.header_items())))
        if url == ALP_ADMIN_ZIP_URL:
            if self.failures.get("download"):
                raise self.failures["download"].pop(0)
            if self.zip_bytes is None:
                raise AssertionError("unexpected download")
            return _Response(self.zip_bytes, {"Last-Modified": "Wed, 23 Sep 2026 10:00:00 GMT"})
        if not url.startswith(SUPABASE_URL):
            raise AssertionError(f"unexpected URL {url}")
        path = url[len(SUPABASE_URL):]
        if method == "GET" and path.startswith("/rest/v1/legal_sources?"):
            if self.failures.get("state"):
                raise self.failures["state"].pop(0)
            source = re.search(r"source_key=eq\.([^&]+)", path).group(1)
            rows = [dict(section_key=k, **{f: v[f] for f in ("content_hash", "status")})
                    for k, v in self.rows.items() if v["source_key"] == source]
            return _Response(json.dumps(rows).encode())
        name = path.rsplit("/", 1)[-1]
        self.counts[name] = self.counts.get(name, 0) + 1
        if name == self.fail_rpc:
            raise urllib.error.HTTPError(url, 400, "Bad Request", {}, io.BytesIO(b'{"message":"boom"}'))
        if self.fail_after and name == self.fail_after[0] and self.counts[name] >= self.fail_after[1]:
            raise urllib.error.HTTPError(url, 500, "Server Error", {}, io.BytesIO(b'{"message":"down"}'))
        if self.failures.get(name):
            raise self.failures[name].pop(0)
        return _Response(json.dumps(getattr(self, "rpc_" + name)(**body)).encode())

    def rpc_corpus_begin_run(self, p_token, p_trigger):
        return "11111111-2222-3333-4444-555555555555"

    def rpc_corpus_preflight_source(self, p_token, p_run_id, p_source_key, p_seen_keys):
        # Mirrors the SQL: coverage of what was ACTIVE before any upsert.
        active = {k for k, v in self.rows.items() if v["source_key"] == p_source_key and v["status"] == "active"}
        covered = len(active & set(p_seen_keys))
        if active and covered < -(-8 * len(active) // 10):
            raise urllib.error.HTTPError("x", 400, "Bad Request", {},
                                         io.BytesIO(b'{"message":"refusing to write"}'))
        return {"active": len(active), "covered": covered}

    def rpc_corpus_upsert_sections(self, p_token, p_run_id, p_sections):
        out = []
        for s in p_sections:
            assert s["content_hash"] == law_hash(s["full_text"])  # the server's check
            row = self.rows.get(s["section_key"])
            change = ("added" if row is None else "amended" if row["content_hash"] != s["content_hash"]
                      else "restored" if row["status"] != "active" else "unchanged")
            self.rows[s["section_key"]] = {"content_hash": s["content_hash"], "status": "active",
                                           "source_key": s["source_key"]}
            out.append({"section_key": s["section_key"], "change": change})
        return out

    def rpc_corpus_finalize_source(self, p_token, p_run_id, p_source_key, p_seen_keys):
        missing = sorted(k for k, v in self.rows.items()
                         if v["source_key"] == p_source_key and v["status"] == "active" and k not in p_seen_keys)
        for k in missing:
            self.rows[k]["status"] = "missing_from_source"
        return {"missing": missing}

    def rpc_corpus_finish_run(self, p_token, p_run_id, p_status, p_summary):
        return None

    def rpc_names(self):
        return [url.rsplit("/", 1)[-1] for method, url, _, _ in self.calls if "/rpc/" in url]


class RefreshTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.zip_path = os.path.join(self.tmp.name, "XML.zip")
        with open(self.zip_path, "wb") as handle:
            handle.write(_fixture_zip())

    def run_refresh(self, argv, net=None, env=None):
        net = net or FakeNetwork()
        out = io.StringIO()
        code = refresh.main(argv, opener=net, env={} if env is None else env, out=out)
        return code, out.getvalue(), net

    def real_sections(self, file_id, key):
        with open(os.path.join(FIXTURES, f"{file_id}.xml"), "rb") as handle:
            return parse_chapter(handle.read(), source_key=key, authority="NYC Admin Code").sections


class RegistryTests(unittest.TestCase):
    def test_minimums_are_ninety_percent_of_real_counts(self):
        mins = {s.key: s.min_sections for s in ALP_SOURCES}
        self.assertEqual(mins["nyc-hmc"], 189)
        self.assertEqual(mins["nyc-rsl"], 22)
        self.assertEqual(mins["nyc-ue"], 8)
        self.assertEqual(mins["nyc-rtc"], 5)
        self.assertEqual(mins["nyc-hrl"], 31)
        self.assertEqual(mins["nyc-rent-control"], 19)

    def test_file_ids_match_the_catalog(self):
        ids = {s.key: s.file_id for s in ALP_SOURCES}
        self.assertEqual(ids, {
            "nyc-hmc": "0-0-0-60027", "nyc-rsl": "0-0-0-201924", "nyc-ue": "0-0-0-47504",
            "nyc-rtc": "0-0-0-47826", "nyc-hrl": "0-0-0-4607", "nyc-rent-control": "0-0-0-228764",
        })

    def test_keys_are_unique_and_valid_for_the_rpc(self):
        keys = [s.key for s in ALP_SOURCES] + [s.key for s in NYS_SOURCES]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertRegex(key, r"^[a-z0-9-]{2,40}$")

    def test_measured_sources_enabled(self):
        # Rent Control was enabled once the full zip measured it (22); the
        # state sources once the live API was measured (2026-10-01).
        enabled = [s.key for s in alp_sources()]
        self.assertEqual(enabled, ["nyc-hmc", "nyc-rsl", "nyc-ue", "nyc-rtc", "nyc-hrl", "nyc-rent-control"])
        self.assertTrue(all(s.real_count for s in ALP_SOURCES if s.enabled))
        self.assertEqual([s.key for s in NYS_SOURCES if s.enabled],
                         ["nys-good-cause", "nys-rpl-7", "nys-rpapl-7", "nys-rpapl-7a", "nys-gol-deposits"])
        self.assertTrue(all(s.real_count for s in NYS_SOURCES))

    def test_anchors_exist_in_the_real_chapters_we_have_whole(self):
        # UE and RTC fixtures are complete chapters, and the kept HMC/RSL/HRL
        # sections cover some anchors; every anchor we CAN check is real.
        present = {}
        for source in ALP_SOURCES:
            path = os.path.join(FIXTURES, f"{source.file_id}.xml")
            if os.path.exists(path):
                with open(path, "rb") as handle:
                    present[source.key] = {s.citation for s in parse_chapter(
                        handle.read(), source_key=source.key, authority="x").sections}
        checked = 0
        for source in ALP_SOURCES:
            for anchor in source.anchors:
                if source.key in ("nyc-ue", "nyc-rtc") or anchor in present.get(source.key, ()):
                    self.assertIn(anchor, present[source.key])
                    checked += 1
        self.assertGreaterEqual(checked, 9)

    def test_unknown_source_is_refused(self):
        with self.assertRaisesRegex(KeyError, "unknown source"):
            alp_sources(["nys-rpl-7"])  # a state source is not an ALP source
        with self.assertRaisesRegex(KeyError, "unknown source"):
            all_sources(["nys-nope"])
        self.assertEqual([s.key for s in all_sources(["nys-rpl-7", "nyc-hmc"])], ["nys-rpl-7", "nyc-hmc"])


class GateTests(RefreshTestCase):
    def _source(self, **kw):
        base = dict(key="nyc-hmc", name="HMC", file_id="0-0-0-60027", anchors=("27-2029",), real_count=7)
        base.update(kw)
        return AlpSource(**base)

    def _result(self, key="nyc-hmc"):
        return refresh.ParseResult(sections=self.real_sections("0-0-0-60027", key))

    def test_real_excerpt_passes_gates_sized_for_it(self):
        self.assertEqual(refresh.gate(self._source(), self._result(), None), [])

    def test_too_few_sections(self):
        problems = refresh.gate(self._source(real_count=211), self._result(), None)
        self.assertEqual(problems, ["nyc-hmc: parsed 7 sections, minimum is 189"])

    def test_missing_anchor(self):
        problems = refresh.gate(self._source(anchors=("27-2029", "27-2030")), self._result(), None)
        self.assertEqual(problems, ["nyc-hmc: anchor § 27-2030 missing"])

    def test_repealed_anchor(self):
        problems = refresh.gate(self._source(anchors=("27-2018",)), self._result(), None)
        self.assertEqual(problems, ["nyc-hmc: anchor § 27-2018 is repealed or empty"])

    def test_empty_unrepealed_section(self):
        result = self._result()
        result.sections[0].paragraphs = []
        problems = refresh.gate(self._source(), result, None)
        self.assertIn("nyc-hmc: § 27-2001 has no text and is not marked repealed", problems)

    def test_no_verified_minimum_is_not_loadable(self):
        problems = refresh.gate(self._source(real_count=None), self._result(), None)
        self.assertIn("no verified section count", problems[0])

    def test_parse_must_cover_80_percent_of_the_library(self):
        current = {f"nyc-hmc:27-99{n:02d}": {"status": "active"} for n in range(10)}
        current.update({s.section_key: {"status": "active"} for s in self._result().sections})
        problems = refresh.gate(self._source(), self._result(), current)
        self.assertEqual(problems, ["nyc-hmc: parse covers 7 of 17 sections already in the library (< 80%)"])

    def test_missing_sections_already_marked_do_not_count(self):
        current = {f"nyc-hmc:27-99{n:02d}": {"status": "missing_from_source"} for n in range(10)}
        self.assertEqual(refresh.gate(self._source(), self._result(), current), [])


class DryRunTests(RefreshTestCase):
    def test_from_zip_needs_no_network_and_no_credentials(self):
        code, out, net = self.run_refresh(
            ["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-ue", "--source", "nyc-rtc"])
        self.assertEqual(code, 0, out)
        self.assertEqual(net.calls, [])
        self.assertIn("| nyc-ue | 9 | n/a | n/a | n/a | n/a | n/a |", out)
        self.assertIn("| nyc-rtc | 6 |", out)
        self.assertIn("(dry run)", out)
        self.assertNotIn("Gates failed", out)

    def test_downloads_the_official_zip_when_not_given_one(self):
        net = FakeNetwork(zip_bytes=_fixture_zip())
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nyc-ue"], net)
        self.assertEqual(code, 0, out)
        self.assertEqual([c[1] for c in net.calls], [ALP_ADMIN_ZIP_URL])
        self.assertIn("Last-Modified: Wed, 23 Sep 2026 10:00:00 GMT", out)

    def test_download_size_is_capped(self):
        with mock.patch.object(refresh, "MAX_ZIP_BYTES", 1000):
            with self.assertRaises(refresh.RefreshError):
                refresh.download_zip(ALP_ADMIN_ZIP_URL, io.BytesIO(), FakeNetwork(zip_bytes=b"x" * 5000))

    def test_real_hmc_excerpt_fails_the_real_gates(self):
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-hmc"])
        self.assertEqual(code, 3)
        self.assertIn("### Gates failed: nothing was written", out)
        self.assertIn("nyc-hmc: parsed 7 sections, minimum is 189", out)

    def test_gate_failure_with_a_library_shows_changes_as_planned_only(self):
        code, out, net = self.run_refresh(
            ["--from-zip", self.zip_path, "--source", "nyc-ue", "--source", "nyc-hmc"], env=ENV)
        self.assertEqual(code, 3)
        self.assertIn("(gates failed)", out)
        self.assertIn("### Would be added", out)
        self.assertNotIn("### Added", out)

    def test_dry_run_with_credentials_reads_but_never_writes(self):
        rows = {s.section_key: {"content_hash": s.content_hash, "status": "active", "source_key": "nyc-ue"}
                for s in self.real_sections("0-0-0-47504", "nyc-ue")}
        rows["nyc-ue:26-521"]["content_hash"] = "0" * 64  # the library holds an older text
        net = FakeNetwork(rows=rows)
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-ue"], net, ENV)
        self.assertEqual(code, 0, out)
        self.assertTrue(all(method == "GET" for method, *_ in net.calls))
        self.assertIn("| nyc-ue | 9 | 0 | 1 | 0 | 8 | 0 |", out)
        self.assertIn("### Would be amended", out)
        self.assertNotIn("### Amended", out)
        self.assertIn("- [NYC Admin Code § 26-521](https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/"
                      "0-0-0-47505) Unlawful eviction", out)

    def test_dump_writes_payloads(self):
        dump = os.path.join(self.tmp.name, "dump")
        code, _, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-rtc", "--dump", dump])
        self.assertEqual(code, 0)
        with open(os.path.join(dump, "nyc-rtc.json"), encoding="utf-8") as handle:
            payloads = json.load(handle)
        self.assertEqual([p["section_key"] for p in payloads][:2], ["nyc-rtc:26-1301", "nyc-rtc:26-1302"])
        self.assertEqual(payloads[0]["notes"], ["* Editor's note: there are two sections designated as § 26-1301."])

    def test_summary_goes_to_github_step_summary_and_file(self):
        step = os.path.join(self.tmp.name, "step.md")
        path = os.path.join(self.tmp.name, "summary.md")
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-ue",
                                         "--summary", path], env={"GITHUB_STEP_SUMMARY": step})
        self.assertEqual(code, 0)
        for p in (step, path):
            with open(p, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), out)


class WriteTests(RefreshTestCase):
    ARGS = ["--from-zip", None, "--source", "nyc-ue", "--source", "nyc-rtc"]

    def args(self):
        return [self.zip_path if a is None else a for a in self.ARGS]

    def test_a_write_needs_all_three_credentials(self):
        code, out, net = self.run_refresh(self.args(), env={"SUPABASE_URL": SUPABASE_URL, "SUPABASE_KEY": "k"})
        self.assertEqual(code, 2)
        self.assertIn("CORPUS_INGEST_TOKEN", out)
        self.assertEqual(net.calls, [])

    def test_initial_load(self):
        output = os.path.join(self.tmp.name, "gh_output")
        code, out, net = self.run_refresh(self.args(), env=dict(ENV, GITHUB_OUTPUT=output))
        self.assertEqual(code, 0, out)
        self.assertEqual(net.rpc_names(), ["corpus_begin_run",
                                           "corpus_preflight_source", "corpus_upsert_sections", "corpus_finalize_source",
                                           "corpus_preflight_source", "corpus_upsert_sections", "corpus_finalize_source",
                                           "corpus_finish_run"])
        self.assertEqual(len(net.rows), 15)
        self.assertIn("| nyc-ue | 9 | 9 | 0 | 0 | 0 | 0 |", out)
        self.assertIn("(live)", out)
        with open(output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "changed=true\n")

    def test_uses_anon_key_and_token_only(self):
        _, _, net = self.run_refresh(self.args(), env=ENV)
        for method, url, body, headers in net.calls:
            self.assertEqual(headers["Apikey"], "anon-key")
            self.assertEqual(headers["Authorization"], "Bearer anon-key")
            if "/rpc/" in url:
                self.assertEqual(body["p_token"], ENV["CORPUS_INGEST_TOKEN"])

    def test_second_identical_run_changes_nothing(self):
        net = FakeNetwork()
        self.run_refresh(self.args(), net, ENV)
        output = os.path.join(self.tmp.name, "gh_output")
        code, out, _ = self.run_refresh(self.args(), net, dict(ENV, GITHUB_OUTPUT=output))
        self.assertEqual(code, 0)
        self.assertIn("| nyc-rtc | 6 | 0 | 0 | 0 | 6 | 0 |", out)
        with open(output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "changed=false\n")

    def test_batches_respect_the_batch_size(self):
        with mock.patch.object(refresh, "BATCH_SIZE", 4):
            _, _, net = self.run_refresh(["--from-zip", self.zip_path, "--source", "nyc-ue"], env=ENV)
        sizes = [len(body["p_sections"]) for _, url, body, _ in net.calls if url.endswith("corpus_upsert_sections")]
        self.assertEqual(sizes, [4, 4, 1])

    def test_one_failing_source_blocks_every_write(self):
        code, out, net = self.run_refresh(
            ["--from-zip", self.zip_path, "--source", "nyc-ue", "--source", "nyc-hmc"], env=ENV)
        self.assertEqual(code, 3)
        self.assertEqual(net.rpc_names(), [])
        self.assertIn("nothing was written", out)

    def test_section_gone_from_source_is_reported_missing_not_deleted(self):
        net = FakeNetwork(rows={s.section_key: {"content_hash": s.content_hash, "status": "active", "source_key": "nyc-ue"}
                                for s in self.real_sections("0-0-0-47504", "nyc-ue")})
        net.rows["nyc-ue:26-530"] = {"content_hash": "0" * 64, "status": "active", "source_key": "nyc-ue"}
        code, out, _ = self.run_refresh(["--from-zip", self.zip_path, "--source", "nyc-ue"], net, ENV)
        self.assertEqual(code, 0, out)
        self.assertEqual(net.rows["nyc-ue:26-530"]["status"], "missing_from_source")
        self.assertIn("### No longer in the source (kept, marked missing)", out)
        self.assertIn("- `nyc-ue:26-530`", out)

    def test_failed_write_closes_the_run_as_failed(self):
        net = FakeNetwork(fail_rpc="corpus_finalize_source")
        code, out, _ = self.run_refresh(self.args(), net, ENV)
        self.assertEqual(code, 1)
        self.assertIn("write failed", out)
        finish = [body for _, url, body, _ in net.calls if url.endswith("corpus_finish_run")]
        self.assertEqual(len(finish), 1)
        self.assertEqual(finish[0]["p_status"], "failed")
        self.assertIn("HTTP 400", finish[0]["p_summary"]["error"])


class FailureTests(RefreshTestCase):
    """Every failure ends in a summary and a clean exit code, never a traceback,
    and a started run is always closed. From the 2026-09-29 review."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(refresh, "_sleep", lambda s: self.sleeps.append(s))
        self.sleeps = []
        patcher.start()
        self.addCleanup(patcher.stop)

    def ue_rtc(self):
        return ["--from-zip", self.zip_path, "--source", "nyc-ue", "--source", "nyc-rtc"]

    def test_transient_errors_are_retried_then_succeed(self):
        net = FakeNetwork(failures={"corpus_upsert_sections": [
            TimeoutError("timed out"),
            urllib.error.HTTPError("x", 503, "Unavailable", {}, io.BytesIO(b"")),
        ]})
        code, out, _ = self.run_refresh(self.ue_rtc(), net, ENV)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sleeps, list(refresh.RETRY_DELAYS))
        self.assertEqual(len(net.rows), 15)

    def test_a_timeout_that_persists_closes_the_run_as_failed(self):
        net = FakeNetwork(failures={"corpus_upsert_sections": [TimeoutError("timed out")] * 3})
        code, out, _ = self.run_refresh(self.ue_rtc(), net, ENV)
        self.assertEqual(code, 1)
        self.assertIn("### Write failed partway", out)
        self.assertIn("timed out", out)
        self.assertNotIn("Gates failed", out)
        finish = [b for _, url, b, _ in net.calls if url.endswith("corpus_finish_run")]
        self.assertEqual([f["p_status"] for f in finish], ["failed"])

    def test_a_refused_token_says_nothing_was_written(self):
        net = FakeNetwork(fail_rpc="corpus_begin_run")
        code, out, _ = self.run_refresh(self.ue_rtc(), net, ENV)
        self.assertEqual(code, 1)
        self.assertIn("### Write failed before anything was written", out)
        self.assertNotIn("partway", out)
        self.assertEqual(net.rows, {})

    def test_http_400_is_not_retried(self):
        net = FakeNetwork(fail_rpc="corpus_upsert_sections")
        code, _, _ = self.run_refresh(self.ue_rtc(), net, ENV)
        self.assertEqual(code, 1)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(net.counts["corpus_upsert_sections"], 1)

    def test_partial_failure_reports_only_what_was_written(self):
        # nyc-ue is written in full, nyc-rtc's first batch fails, nyc-rtc gets nothing.
        output = os.path.join(self.tmp.name, "gh_output")
        net = FakeNetwork(fail_after=("corpus_upsert_sections", 2))
        code, out, _ = self.run_refresh(self.ue_rtc(), net, dict(ENV, GITHUB_OUTPUT=output))
        self.assertEqual(code, 1)
        self.assertIn("| nyc-ue | 9 | 9 | 0 | 0 | 0 | 0 |", out)
        self.assertIn("| nyc-rtc | 6 | 0 | 0 | 0 | 0 | stopped partway |", out)
        self.assertEqual(sorted(k for k in net.rows if k.startswith("nyc-rtc")), [])
        self.assertIn("\n### Added\n", out)
        self.assertIn("§ 26-521]", out)
        self.assertNotIn("§ 26-1301]", out)  # planned but never written: not listed
        with open(output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "changed=true\n")

    def test_a_source_not_reached_is_marked_not_attempted(self):
        net = FakeNetwork(fail_after=("corpus_preflight_source", 2))
        code, out, _ = self.run_refresh(self.ue_rtc(), net, ENV)
        self.assertEqual(code, 1)
        self.assertIn("| nyc-rtc | 6 | not attempted | not attempted |", out)

    def test_server_preflight_refuses_before_any_upsert(self):
        # The library holds 10 active UE sections the parse does not contain
        # (0 of 10 covered). The client gate is stubbed out to stand in for a
        # client bug, so only the server's preflight stands in the way.
        rows = {f"nyc-ue:26-9{n}": {"content_hash": "0" * 64, "status": "active", "source_key": "nyc-ue"}
                for n in range(10)}
        net = FakeNetwork(rows=rows)
        with mock.patch.object(refresh, "gate", lambda *a: []):  # simulate a client gate bug
            code, out, _ = self.run_refresh(["--from-zip", self.zip_path, "--source", "nyc-ue"], net, ENV)
        self.assertEqual(code, 1)
        self.assertEqual(net.counts.get("corpus_upsert_sections", 0), 0)
        self.assertTrue(all(v["status"] == "active" for v in net.rows.values()))
        self.assertIn("refusing to write", out)

    def test_download_failure_exits_2_with_a_summary(self):
        net = FakeNetwork(failures={"download": [urllib.error.URLError("name resolution failed")]})
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nyc-ue"], net)
        self.assertEqual(code, 2)
        self.assertIn("### Could not run: nothing was written", out)
        self.assertIn("name resolution failed", out)

    def test_missing_migration_exits_2_and_names_the_likely_cause(self):
        net = FakeNetwork(failures={"state": [urllib.error.HTTPError(
            "x", 400, "Bad Request", {}, io.BytesIO(b'{"message":"column legal_sources.section_key does not exist"}'))]})
        code, out, _ = self.run_refresh(["--from-zip", self.zip_path, "--source", "nyc-ue"], net, ENV)
        self.assertEqual(code, 2)
        self.assertIn("20260929 migration", out)
        self.assertEqual(net.rpc_names(), [])

    def test_corrupt_zip_fails_the_gate(self):
        with open(self.zip_path, "wb") as handle:
            handle.write(b"not a zip")
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", self.zip_path, "--source", "nyc-ue"])
        self.assertEqual(code, 3)
        self.assertIn("cannot read the zip", out)

    def test_missing_local_zip_exits_2(self):
        code, out, _ = self.run_refresh(["--dry-run", "--from-zip", "/nonexistent/XML.zip"])
        self.assertEqual(code, 2)
        self.assertIn("no such file", out)

    def test_github_output_is_written_even_when_nothing_could_run(self):
        output = os.path.join(self.tmp.name, "gh_output")
        net = FakeNetwork(failures={"download": [OSError("reset")] })
        self.run_refresh(["--source", "nyc-ue"], net, dict(ENV, GITHUB_OUTPUT=output))
        with open(output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "changed=false\n")


class CliTests(RefreshTestCase):
    def test_unknown_source(self):
        code, out, _ = self.run_refresh(["--dry-run", "--source", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown source(s): nope", out)

    def test_new_token_prints_only_the_hash_in_sql(self):
        code, out, net = self.run_refresh(["--new-token"])
        self.assertEqual(code, 0)
        self.assertEqual(net.calls, [])
        token = re.search(r"^    ([0-9a-f]{64})$", out, re.M).group(1)
        digest = re.search(r"VALUES \('([0-9a-f]{64})'", out).group(1)
        self.assertEqual(digest, hashlib.sha256(token.encode()).hexdigest())
        sql_line = next(line for line in out.splitlines() if "INSERT" in line)
        self.assertNotIn(token, sql_line)


if __name__ == "__main__":
    unittest.main()

