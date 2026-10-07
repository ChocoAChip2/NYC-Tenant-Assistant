"""Refresh the elected officials the street lookup shows.

    python -m tools.officials.refresh --dry-run     # fetch, check, report; write nothing
    python -m tools.officials.refresh               # the real thing

WHEN (owner, 2026-10-07): after elections, leaving about a month for
results to be called and certified (and NYC's ranked-choice counts and any
runoffs). The workflow runs on the 15th of December and January, plus the
15th of April, July and October to catch special elections and vacancies.

SOURCES (official, nothing scraped)
  City Council     NYC Open Data "City Council Members" (uvw5-9znb), rows
                   whose term has not ended. No key.
  State Assembly   NY Senate Open Legislation API, /api/3/members/{session}/
  State Senate     {chamber}?full=true -- incumbents only. Needs
                   NYSENATE_API_KEY; without it those two are skipped and
                   the summary says so.

GATES (nothing is written for an office that fails one)
  - at least MIN_COUNT[office] districts (a feed that comes back half
    empty must not wipe the table), every district in range,
  - one person per district.

Writes go through officials_replace() (20261007 migration), with the
public key plus CORPUS_INGEST_TOKEN, like tools.corpus. When anything
changed, `changed=true` goes to $GITHUB_OUTPUT so the workflow opens an
issue listing the changes for a person to look at.

Exit codes: 0 ok, 1 a write failed, 2 could not run, 3 a gate refused.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from tools.corpus.refresh import RefreshError, Supabase

USER_AGENT = "SideKickTidbit-officials/1.0 (+https://github.com/ChocoAChip2/NYC-Tenant-Assistant)"
COUNCIL_URL = "https://data.cityofnewyork.us/resource/uvw5-9znb.json"
NYS_MEMBERS_URL = "https://legislation.nysenate.gov/api/3/members/{session}/{chamber}"
TIMEOUT = 60

DISTRICTS = {"council": 51, "assembly": 150, "state_senate": 63}
# A real list is never this short; a vacancy or two is normal.
MIN_COUNT = {"council": 45, "assembly": 140, "state_senate": 56}
SOURCES = {
    "council": "NYC Open Data, City Council Members",
    "assembly": "NY Senate Open Legislation, Assembly members",
    "state_senate": "NY Senate Open Legislation, Senate members",
}
TITLES = {"council": "City Council", "assembly": "State Assembly", "state_senate": "State Senate"}


def _get(url: str, params: dict, opener=urllib.request.urlopen, secret: str = ""):
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RefreshError(f"{url.split('?')[0]} -> HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        message = f"{url.split('?')[0]} failed: {getattr(exc, 'reason', exc)}"
        raise RefreshError(message.replace(secret, "<key>") if secret else message) from None


def session_year(today: date) -> int:
    """The Legislature's two-year session starts in odd years."""
    return today.year if today.year % 2 else today.year - 1


def fetch_council(today: date, opener=urllib.request.urlopen) -> list[dict]:
    rows = _get(COUNCIL_URL, {"$where": f"term_end >= '{today.isoformat()}'", "$limit": "200"}, opener)
    out = []
    for row in rows if isinstance(rows, list) else []:
        try:
            district = int(str(row.get("district")).strip())
        except (TypeError, ValueError):
            continue
        name = " ".join(str(row.get("name") or "").split())
        if name:
            out.append({"district": district, "name": name})
    return out


def fetch_state(chamber: str, key: str, today: date, opener=urllib.request.urlopen) -> list[dict]:
    body = _get(NYS_MEMBERS_URL.format(session=session_year(today), chamber=chamber),
                {"full": "true", "limit": "1000", "key": key}, opener, secret=key)
    if not isinstance(body, dict) or not body.get("success"):
        raise RefreshError(f"NY Senate API {chamber}: unsuccessful response")
    out = []
    for item in ((body.get("result") or {}).get("items")) or []:
        if not item.get("incumbent"):
            continue
        try:
            district = int(item.get("districtCode"))
        except (TypeError, ValueError):
            continue
        name = " ".join(str(item.get("fullName") or "").split())
        if name:
            out.append({"district": district, "name": name})
    return out


def gate(office: str, rows: list[dict]) -> list[str]:
    problems = []
    districts = [r["district"] for r in rows]
    if len(set(districts)) < MIN_COUNT[office]:
        problems.append(f"{office}: {len(set(districts))} districts, minimum is {MIN_COUNT[office]}")
    out_of_range = sorted(d for d in districts if not 1 <= d <= DISTRICTS[office])
    if out_of_range:
        problems.append(f"{office}: districts out of range: {out_of_range}")
    seen, dupes = set(), set()
    for d in districts:
        (dupes if d in seen else seen).add(d)
    if dupes:
        problems.append(f"{office}: more than one person for district(s) {sorted(dupes)}")
    return problems


def main(argv=None, *, env=None, opener=urllib.request.urlopen, out=None, today: date | None = None) -> int:
    env = os.environ if env is None else env
    out = out or sys.stdout
    today = today or date.today()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="fetch and check only; write nothing")
    parser.add_argument("--summary", metavar="PATH", help="also write the Markdown summary here")
    args = parser.parse_args(argv)

    lines = [f"## Elected officials refresh, {today.isoformat()}{' (dry run)' if args.dry_run else ''}", ""]
    token = env.get("CORPUS_INGEST_TOKEN", "").strip()
    key = env.get("NYSENATE_API_KEY", "").strip()
    supabase = None
    if not args.dry_run:
        url, anon = env.get("SUPABASE_URL", "").strip(), env.get("SUPABASE_KEY", "").strip()
        if not (url and anon and token):
            print("SUPABASE_URL, SUPABASE_KEY and CORPUS_INGEST_TOKEN are required to write (or use --dry-run).", file=out)
            return 2
        supabase = Supabase(url, anon, opener=opener)

    fetched: dict[str, list[dict]] = {}
    skipped = []
    try:
        fetched["council"] = fetch_council(today, opener)
        if key:
            fetched["assembly"] = fetch_state("assembly", key, today, opener)
            fetched["state_senate"] = fetch_state("senate", key, today, opener)
        else:
            skipped = ["assembly", "state_senate"]
    except RefreshError as exc:
        print(f"Could not fetch: {exc}", file=out)
        return 2

    problems = [p for office, rows in fetched.items() for p in gate(office, rows)]
    lines.append("| Office | Districts with a member |")
    lines.append("|---|---|")
    for office, rows in fetched.items():
        lines.append(f"| {TITLES[office]} | {len(rows)} of {DISTRICTS[office]} |")
    if skipped:
        lines += ["", "Skipped (NYSENATE_API_KEY not set): " + ", ".join(TITLES[o] for o in skipped)]
    if problems:
        lines += ["", "**Refused, nothing written:**"] + [f"- {p}" for p in problems]
        _emit(lines, args.summary, env, out, changed=False)
        return 3

    changed = False
    if supabase is not None:
        for office, rows in fetched.items():
            try:
                changes = supabase.rpc("officials_replace", p_token=token, p_office=office,
                                       p_source=SOURCES[office], p_rows=sorted(rows, key=lambda r: r["district"])) or []
            except RefreshError as exc:
                lines += ["", f"**Write failed for {TITLES[office]}:** {str(exc).replace(token, '<token>')}"]
                _emit(lines, args.summary, env, out, changed=changed)
                return 1
            if changes:
                changed = True
                lines += ["", f"### {TITLES[office]}: {len(changes)} change(s)"]
                for c in sorted(changes, key=lambda c: c.get("district", 0)):
                    if c.get("change") == "changed":
                        lines.append(f"- District {c['district']}: {c.get('old')} -> {c.get('name')}")
                    elif c.get("change") == "removed":
                        lines.append(f"- District {c['district']}: {c.get('old')} removed (vacant or not listed)")
                    else:
                        lines.append(f"- District {c['district']}: {c.get('name')} added")
        if not changed:
            lines += ["", "No changes."]
    _emit(lines, args.summary, env, out, changed=changed)
    return 0


def _emit(lines, summary_path, env, out, changed):
    text = "\n".join(lines) + "\n"
    print(text, file=out)
    if summary_path:
        with open(summary_path, "w", encoding="utf-8") as handle:
            handle.write(text)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    sys.exit(main())
