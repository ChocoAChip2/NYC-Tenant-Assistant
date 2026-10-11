# SideKick Tidbit: current handoff

Updated: 2026-10-11 (America/New_York). Status is taken from git, `log/` and the test runs recorded under Verification.
Location: `~/Desktop/Github/nyc-tenant-assistant-push-2`, branch `main` at `45fcf05` (PR #59, 2026-10-11).

## Objective

Ship emailed sign-in codes and the stronger password rule (owner chose it on 2026-10-11, lifting the feature hold for this one change). Built on `feat/email-codes-and-passwords`; the owner merges after step 1 of the release order below.

Done when: merged, the dashboard steps are applied, and a real inbox receives a working code.

Merged on 2026-10-11: PR #57 (workflow setup), #58 (`/law` index), #59 ("Your data"). `main` is at `45fcf05`. Their live checks are still open (see Next action).

## Decisions in force

- Audience is individual tenants first.
- 2026-10-01: hold new features; prioritize testing, debugging and a clean `main`.
- 2026-10-07: street + borough is the default search (exact address optional) and a read-only Resources tab is approved. Both shipped in PR #53. Community Q&A and the scaling fixes are on hold. The community board waits for a layout (posting 18+, no AI-written answers).
- 2026-10-11: `/law` index defaults approved: every section as number + title; City then State, one closed group per law; a number-only "Go to a section" box (no full-text search); repealed and dropped sections labelled, never hidden; linked from the section page and Resources, not the header nav.
- 2026-10-11: second sign-in step is a 6-digit emailed code (no authenticator app, no SMS), optional, turned on in Settings. Passwords: minimum 8 with lowercase, uppercase, digit and symbol. Existing weak passwords are forced to change at next login, no skip. Losing access to email has no separate recovery. Dashboard changes are made from Claude Desktop, one named change at a time with the owner's go-ahead.
- 2026-10-11: "Your situation" becomes "Your data": a pill in the composer toolbar left of Send, filled by the assistant from the tenant's own messages, correctable by the tenant, no send gate, no database change. Details: `docs/frontend/case-workspace.md`.
- 2026-10-08: the owner adopted the Codex review pass, including the privacy line "Your details stay confidential. Used for your account."

## Recently completed

PRs #53, #55 and #56 (street lookup and Resources; case workspace and reviewed RA-81 drafts; migration names matched to live versions). The legal library is live with a quarterly refresh. Details: `log/2026-10-07-*`, `log/2026-10-08-*`.

## Environment (owner's Mac, observed 2026-10-11)

- `venv/bin/python` runs Python 3.12.15 (it follows Homebrew's `python@3.12`; `venv/pyvenv.cfg` still records 3.12.2 from creation). Flask, Supabase, Flask-Limiter, Flask-WTF and cryptography import. That's dependency discovery, not a test result.
- Claude Code 2.1.220 is installed. Reading `AGENTS.md` directly needs 2.1.277+, so keep the `CLAUDE.md` import.

## Verification

- Full suite on the Mac, 2026-10-11: **886 OK (1 skipped)** on `docs/agent-workflow` (`1f8a682`), **908 OK (1 skipped)** on `feat/law-index`.
- Sign-in codes and password rule (`feat/email-codes-and-passwords`): **987 OK (1 skipped)**. Pages rendered in headless Chrome against an in-memory app. **No real email was sent**; mail delivery, the hourly limit and the email template are unverified. Details: `log/2026-10-11-email-codes-and-password-rule.txt`.
- "Your data" (`feat/your-data-button`): **930 OK (1 skipped)**. Rendered and scripted in headless Chrome against the in-memory app. No real model call was made, so extraction quality is untested. Details: `log/2026-10-11-your-data-button.txt`.
- `/law` index (merged): rendered locally with a faked library (light/dark, 1280 px and a 390 px frame). Live library counted read-only: 426 sections in 11 laws. Not yet opened on Render; keyboard use not driven by hand. Details: `log/2026-10-11-law-index.txt`.
- Workflow setup (2026-10-11): changes agent instructions, docs, two Python comments/docstrings and one workflow notice string; app logic is unchanged. Checks: the two Python files compile, the workflow YAML parses, no stale `docs/HANDOFF.md` references, `git diff --check` passes. The test suite was not run for it.

## Unresolved

- Owner items from the old checklist, not confirmed done: add GitHub secret `NYSENATE_API_KEY` (until then the quarterly refresh skips state law), and run the post-deploy checks on Render (sign-up with `password` is refused; one real `/building` lookup works).
- A brand-new database can't be built from `supabase/migrations/` alone: the original conversations/messages tables predate tracked migrations. Fixing this is separate engineering work; it would remove the need for live-DB migration tests.
- The live library holds Real Property Law `233-b*2` (Campgrounds), which the `/law/<citation>` number pattern rejects (404). The index lists it without a link. Owner decision: widen the pattern, or leave it.
- Not yet confirmed in a live session: that Claude Code loads `CLAUDE.md` and the imported `AGENTS.md` here (check once with `/memory` in Claude Code started in this folder).

## Next action

1. Owner, before merging the sign-in codes PR: confirm the project sends auth email through its own mail server and raise the hourly auth-email limit. If there is no custom mail server, the codes feature is blocked until there is (the password rule is not).
2. Merge and deploy, then from Claude Desktop, one change at a time: the "Magic Link" template to a code email containing `{{ .Token }}`, code expiry at 1 hour or less, and the dashboard password rule (minimum 8; lowercase, uppercase, digits and symbols).
3. Live check: turn codes on for a real account, sign out and in; confirm the code arrives promptly, works, and that a wrong or old code is refused.
4. Still open from earlier merges: `/law` on Render shows 426 sections; a real chat fills in "Your data" after a reply and a suggestion does not appear under "Already done".
5. Confirm or close the owner items above, then set the next objective here.
