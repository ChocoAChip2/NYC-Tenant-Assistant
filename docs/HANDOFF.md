# SideKick Tidbit: current handoff

Updated: 2026-10-11 (America/New_York). Status is taken from git and `log/`; it was not re-tested today.
Location: `~/Desktop/Github/nyc-tenant-assistant-push-2`, branch `main` at `08c6790` (PR #56, 2026-10-08).

## Objective

Active maintenance task: review and land the uncommitted workflow setup (below). No product feature is selected; the owner picks the next one, recorded here with its completion criteria.

## Decisions in force

- Audience is individual tenants first.
- 2026-10-01: hold new features; prioritize testing, debugging and a clean `main`.
- 2026-10-07: street + borough is the default search (exact address optional) and a read-only Resources tab is approved. Both shipped in PR #53. Community Q&A and the scaling fixes are on hold. The community board waits for a layout (posting 18+, no AI-written answers).
- 2026-10-08: the owner adopted the Codex review pass, including the privacy line "Your details stay confidential. Used for your account."

## Recently completed

PRs #53, #55 and #56 (street lookup and Resources; case workspace and reviewed RA-81 drafts; migration names matched to live versions). The legal library is live with a quarterly refresh. Details: `log/2026-10-07-*`, `log/2026-10-08-*`.

## Environment (owner's Mac, observed 2026-10-11)

- `venv/bin/python` runs Python 3.12.15 (it follows Homebrew's `python@3.12`; `venv/pyvenv.cfg` still records 3.12.2 from creation). Flask, Supabase, Flask-Limiter, Flask-WTF and cryptography import. That's dependency discovery, not a test result.
- Claude Code 2.1.220 is installed. Reading `AGENTS.md` directly needs 2.1.277+, so keep the `CLAUDE.md` import.

## Verification

- Last recorded full suite: **851 OK** (`log/2026-10-07-street-lookup-and-resources.txt`). The suite was not run after PR #56 per the logs, and not on 2026-10-11.
- Workflow setup (2026-10-11): changes agent instructions, docs, two Python comments/docstrings and one workflow notice string; app logic is unchanged. Checks: the two Python files compile, the workflow YAML parses, no stale `docs/HANDOFF.md` references, `git diff --check` passes. The test suite was not run for it.

## Unresolved

- Owner items from the old checklist, not confirmed done: add GitHub secret `NYSENATE_API_KEY` (until then the quarterly refresh skips state law), and run the post-deploy checks on Render (sign-up with `password` is refused; one real `/building` lookup works).
- A brand-new database can't be built from `supabase/migrations/` alone: the original conversations/messages tables predate tracked migrations. Fixing this is separate engineering work; it would remove the need for live-DB migration tests.
- Not yet confirmed in a live session: that Claude Code loads `CLAUDE.md` and the imported `AGENTS.md` here (check once with `/memory` in Claude Code started in this folder).

## Pending changes (workflow setup, 2026-10-11, uncommitted; replace with the PR link once merged)

- New `AGENTS.md` and `CLAUDE.md` (imports it); this file rewritten.
- Old handoff renamed to `docs/legal-library-record.md` (marked historical); its six references repointed.

## Next action

1. Check branch, HEAD, `git status` and the diff. Create or reuse the branch `docs/agent-workflow`, stage only these paths: `AGENTS.md CLAUDE.md docs/HANDOFF.md docs/legal-library-record.md docs/grounded-sources.md docs/legal-grounding.md tests/test_corpus_alp.py tools/corpus/registry.py .github/workflows/refresh-legal-library.yml`. Inspect the staged diff, then commit and open a PR.
2. Separately, run `venv/bin/python -m unittest discover -s tests` on the Mac. The suite hasn't been confirmed green since #56, so don't call the app baseline green until it is.
3. Confirm or close the two owner items above, then set the next objective here.
