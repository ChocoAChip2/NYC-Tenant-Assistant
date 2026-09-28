"""Check the legal library against the official source, and load what changed.

    python -m tools.corpus.refresh --dry-run                  # download, parse, gate, report
    python -m tools.corpus.refresh --dry-run --from-zip XML.zip
    python -m tools.corpus.refresh --source nyc-hmc --dump out/
    python -m tools.corpus.refresh                            # the real thing
    python -m tools.corpus.refresh --new-token                # make an ingest token

Every run downloads ALP's Admin Code bulk XML (or reads --from-zip),
parses each enabled source, and puts the parse through sanity gates: a
minimum section count, anchor sections present and in force, no empty
section that is not marked repealed, and at least 80% of what the library
already holds. If ANY source fails, nothing is written for ANY source.

Writes go through the token-checked corpus_* RPCs (see the 20260929
migration), called with the public anon key plus CORPUS_INGEST_TOKEN.
The service-role key is never used. Environment:

    SUPABASE_URL, SUPABASE_KEY   read current state (optional for --dry-run)
    CORPUS_INGEST_TOKEN          required to write

The Markdown summary goes to stdout, to $GITHUB_STEP_SUMMARY when set,
and to --summary PATH. When anything changed, `changed=true` is written to
$GITHUB_OUTPUT so the workflow can open an issue.

Exit codes: 0 ok, 1 a write failed partway (batches already sent stay
written; they are idempotent, and nothing was marked missing), 2 bad
usage or missing credentials, 3 a gate failed (nothing written).

Standard library only, like the rest of tools/corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.corpus.alp import AlpParseError, ParseResult, parse_chapter, read_chapter_from_zip
from tools.corpus.registry import ALP_ADMIN_ZIP_URL, AlpSource, alp_sources

USER_AGENT = "SideKickTidbit-legal-library/1.0 (+https://github.com/ChocoAChip2/NYC-Tenant-Assistant)"
MAX_ZIP_BYTES = 250 * 1024 * 1024  # the real file is ~65 MB
DOWNLOAD_TIMEOUT = 300
RPC_TIMEOUT = 120
BATCH_SIZE = 25  # the RPC refuses more than 50
MIN_SEEN_FRACTION = 0.8  # same threshold corpus_finalize_source enforces


class RefreshError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Network (everything goes through `opener`, so tests fake it by URL)
# ---------------------------------------------------------------------------

def download_zip(url: str, dest, opener=urllib.request.urlopen) -> str | None:
    """Stream the bulk zip into `dest` (a binary file). Returns Last-Modified."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=DOWNLOAD_TIMEOUT) as response:
        total = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_ZIP_BYTES:
                raise RefreshError(f"download exceeds {MAX_ZIP_BYTES} bytes; refusing")
            dest.write(block)
        return response.headers.get("Last-Modified")


class Supabase:
    """Just enough PostgREST: read legal_sources, call the corpus_* RPCs."""

    def __init__(self, url: str, key: str, opener=urllib.request.urlopen):
        self.base = url.rstrip("/")
        self.key = key
        self.opener = opener

    def _request(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with self.opener(request, timeout=RPC_TIMEOUT) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RefreshError(f"{method} {path.split('?')[0]} -> HTTP {exc.code}: {detail}") from exc
        return json.loads(raw) if raw.strip() else None

    def current_state(self, source_key: str) -> dict[str, dict]:
        query = urllib.parse.urlencode({
            "select": "section_key,content_hash,status",
            "source_key": f"eq.{source_key}",
            "limit": "10000",
        })
        rows = self._request("GET", f"/rest/v1/legal_sources?{query}") or []
        return {row["section_key"]: row for row in rows}

    def rpc(self, name: str, **params):
        return self._request("POST", f"/rest/v1/rpc/{name}", params)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def gate(source: AlpSource, result: ParseResult, current: dict[str, dict] | None) -> list[str]:
    """Reasons this parse must not be written. Empty means it may be."""
    problems: list[str] = []
    sections = result.sections
    by_citation = {s.citation: s for s in sections}

    minimum = source.min_sections
    if minimum is None:
        problems.append(f"{source.key}: no verified section count, so no minimum to check against; not loadable yet")
    elif len(sections) < minimum:
        problems.append(f"{source.key}: parsed {len(sections)} sections, minimum is {minimum}")

    for anchor in source.anchors:
        section = by_citation.get(anchor)
        if section is None:
            problems.append(f"{source.key}: anchor § {anchor} missing")
        elif section.repealed or not section.paragraphs:
            problems.append(f"{source.key}: anchor § {anchor} is repealed or empty")

    for section in sections:
        if not section.repealed and not section.paragraphs:
            problems.append(f"{source.key}: § {section.citation} has no text and is not marked repealed")

    if current:
        active = {k for k, row in current.items() if row.get("status") == "active"}
        seen = active & {s.section_key for s in sections}
        if active and len(seen) < MIN_SEEN_FRACTION * len(active):
            problems.append(
                f"{source.key}: parse covers {len(seen)} of {len(active)} sections already in the library (< 80%)"
            )
    return problems


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class SourceReport:
    source: AlpSource
    result: ParseResult
    problems: list[str] = field(default_factory=list)
    changes: dict[str, list] = field(default_factory=lambda: {
        "added": [], "amended": [], "restored": [], "unchanged": [], "missing_from_source": [],
    })
    compared: bool = False

    @property
    def changed(self) -> bool:
        return any(self.changes[k] for k in ("added", "amended", "restored", "missing_from_source"))


def plan_changes(report: SourceReport, current: dict[str, dict]) -> None:
    """What a write would do, computed locally from the current hashes."""
    report.compared = True
    for section in report.result.sections:
        row = current.get(section.section_key)
        if row is None:
            kind = "added"
        elif row.get("content_hash") != section.content_hash:
            kind = "amended"
        elif row.get("status") != "active":
            kind = "restored"
        else:
            kind = "unchanged"
        report.changes[kind].append(section.section_key)
    seen = {s.section_key for s in report.result.sections}
    report.changes["missing_from_source"] = sorted(
        k for k, row in current.items() if row.get("status") == "active" and k not in seen
    )


def render_summary(reports: list[SourceReport], *, origin: str, mode: str, today: str) -> str:
    lines = [f"## Legal library refresh, {today} ({mode})", "", f"Source: {origin}", ""]
    lines += [
        "| Source | Parsed | Added | Amended | Restored | Unchanged | Missing |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        if r.compared:
            counts = " | ".join(str(len(r.changes[k])) for k in
                                ("added", "amended", "restored", "unchanged", "missing_from_source"))
        else:
            counts = " | ".join(["n/a"] * 5)
        lines.append(f"| {r.source.key} | {len(r.result.sections)} | {counts} |")

    problems = [p for r in reports for p in r.problems]
    if problems:
        lines += ["", "### Gates failed: nothing was written", ""] + [f"- {p}" for p in problems]

    # A write (even one that failed partway) reports what happened; a dry
    # run or a gate failure reports what WOULD happen, and says so.
    headings = (("amended", "Amended", "Would be amended"),
                ("added", "Added", "Would be added"),
                ("restored", "Back in the source", "Would be back in the source"),
                ("missing_from_source", "No longer in the source (kept, marked missing)",
                 "Would be marked missing (no longer in the source)"))
    sections_by_key = {s.section_key: s for r in reports for s in r.result.sections}
    for kind, done, planned in headings:
        heading = done if mode in ("live", "write failed") else planned
        keys = [k for r in reports for k in r.changes[kind]]
        if not keys or (kind == "added" and len(keys) > 50):
            if keys:
                lines += ["", f"### {heading}: {len(keys)} sections (initial load; not listed)"]
            continue
        lines += ["", f"### {heading}", ""]
        for key in keys:
            s = sections_by_key.get(key)
            if s is None:
                lines.append(f"- `{key}`")
            else:
                amended = f" (last amended {s.last_amended})" if s.last_amended else ""
                lines.append(f"- [{s.authority} § {s.citation}]({s.official_url}) {s.title}{amended}")

    warnings = [(r.source.key, w) for r in reports for w in r.result.warnings]
    if warnings:
        lines += ["", "### Parser warnings", ""] + [f"- {k}: {w}" for k, w in warnings]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def new_token() -> str:
    token = secrets.token_hex(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return (
        "Ingest token (paste into the GitHub secret CORPUS_INGEST_TOKEN; do not store it anywhere else):\n\n"
        f"    {token}\n\n"
        "Then run this in the Supabase SQL editor. It holds only the hash:\n\n"
        f"    INSERT INTO public.corpus_ingest_tokens (token_hash, label) VALUES ('{digest}', 'github-actions');\n"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.corpus.refresh", description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="parse, gate and report; write nothing")
    p.add_argument("--source", action="append", metavar="KEY", help="only this source (repeatable)")
    p.add_argument("--from-zip", metavar="PATH", help="use a local copy of the ALP Admin XML.zip")
    p.add_argument("--dump", metavar="DIR", help="write each source's parsed payloads as JSON here")
    p.add_argument("--summary", metavar="PATH", help="also write the Markdown summary here")
    p.add_argument("--trigger", default="manual", help="recorded on the run (e.g. schedule)")
    p.add_argument("--new-token", action="store_true", help="print a fresh ingest token and its SQL, then exit")
    return p


def main(argv=None, *, opener=urllib.request.urlopen, env=None, out=None) -> int:
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    args = _parser().parse_args(argv)

    if args.new_token:
        out.write(new_token())
        return 0

    try:
        sources = alp_sources(args.source)
    except KeyError as exc:
        out.write(f"error: {exc.args[0]}\n")
        return 2

    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_KEY")
    token = env.get("CORPUS_INGEST_TOKEN")
    if not args.dry_run and not (url and key and token):
        out.write("error: a write needs SUPABASE_URL, SUPABASE_KEY and CORPUS_INGEST_TOKEN (or use --dry-run)\n")
        return 2
    db = Supabase(url, key, opener) if url and key else None

    with tempfile.TemporaryDirectory() as tmp:
        if args.from_zip:
            zip_path, origin = args.from_zip, f"local file `{os.path.basename(args.from_zip)}`"
        else:
            zip_path = os.path.join(tmp, "XML.zip")
            with open(zip_path, "wb") as handle:
                last_modified = download_zip(ALP_ADMIN_ZIP_URL, handle, opener)
            origin = f"{ALP_ADMIN_ZIP_URL} (Last-Modified: {last_modified or 'not given'})"

        reports: list[SourceReport] = []
        for source in sources:
            try:
                result = parse_chapter(
                    read_chapter_from_zip(zip_path, source.file_id),
                    source_key=source.key,
                    authority=source.authority,
                )
            except AlpParseError as exc:
                result = ParseResult(sections=[])
                report = SourceReport(source, result, problems=[f"{source.key}: {exc}"])
                reports.append(report)
                continue
            reports.append(SourceReport(source, result))

    for report in reports:
        current = db.current_state(report.source.key) if db else None
        if not report.problems:
            report.problems = gate(report.source, report.result, current)
        if current is not None:
            plan_changes(report, current)

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
        for report in reports:
            path = os.path.join(args.dump, f"{report.source.key}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([s.as_payload() for s in report.result.sections], handle, ensure_ascii=False, indent=1)

    failed = any(r.problems for r in reports)
    mode = "dry run" if args.dry_run else ("gates failed" if failed else "live")
    exit_code = 0

    if not args.dry_run and not failed:
        try:
            _write(db, token, reports, args.trigger)
        except RefreshError as exc:
            mode = "write failed"
            exit_code = 1
            reports[0].problems.append(f"write failed: {exc}")

    if failed:
        exit_code = 3

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = render_summary(reports, origin=origin, mode=mode, today=today)
    _emit(summary, args.summary, env, out)
    changed = exit_code == 0 and not args.dry_run and any(r.changed for r in reports)
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")
    return exit_code


def _write(db: Supabase, token: str, reports: list[SourceReport], trigger: str) -> None:
    run_id = db.rpc("corpus_begin_run", p_token=token, p_trigger=trigger)
    try:
        for report in reports:
            for kind in report.changes:
                report.changes[kind] = []
            report.compared = True
            payloads = [s.as_payload() for s in report.result.sections]
            for start in range(0, len(payloads), BATCH_SIZE):
                results = db.rpc("corpus_upsert_sections", p_token=token, p_run_id=run_id,
                                 p_sections=payloads[start:start + BATCH_SIZE])
                for item in results or []:
                    report.changes[item["change"]].append(item["section_key"])
            final = db.rpc("corpus_finalize_source", p_token=token, p_run_id=run_id,
                           p_source_key=report.source.key,
                           p_seen_keys=[s.section_key for s in report.result.sections])
            report.changes["missing_from_source"] = list((final or {}).get("missing") or [])
        summary = {r.source.key: {k: len(v) for k, v in r.changes.items()} for r in reports}
        db.rpc("corpus_finish_run", p_token=token, p_run_id=run_id, p_status="succeeded", p_summary=summary)
    except RefreshError as exc:
        try:
            db.rpc("corpus_finish_run", p_token=token, p_run_id=run_id, p_status="failed",
                   p_summary={"error": str(exc)[:1000]})
        except RefreshError:
            pass
        raise


def _emit(summary: str, path: str | None, env, out) -> None:
    out.write(summary)
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(summary)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary)


if __name__ == "__main__":
    sys.exit(main())
