# SideKick Tidbit: current handoff

Updated: 2026-10-11 (America/New_York). Status is taken from git, `log/` and the test runs recorded under Verification.
Location: `~/Desktop/Github/nyc-tenant-assistant-push-2`, branch `main` at `b7d7bae` (PR #63, 2026-10-11).

## Objective

Make the site readable in English, Spanish, Chinese (Simplified and Traditional), Swahili and French (owner, 2026-10-11). In slices, each with English unchanged. The first slice (foundation) is merged; see `docs/frontend/translations.md` for what is and isn't done.

Done when: every page is wrapped, each language's catalogue is filled, a fluent reader has approved its `REVIEW` entries, and it has been added to `i18n.LIVE_LANGUAGES`.

Merged on 2026-10-11: PRs #57 to #63 (workflow setup, `/law` index, "Your data", sign-in codes and password rule, new-chat greeting, "Did you mean", usage counts), then the phone chat drawer (PR #64, written with Codex) and the translation foundation (PR #65), combined and checked together on `integrate/drawer-and-i18n`. Several of them still have owner steps open; see Next action.

Waiting, not started: emailed PDF summary of a conversation. It needs a sending domain and email provider, and the owner to lift the feature hold for it.

## Decisions in force

- Audience is individual tenants first.
- 2026-10-01: hold new features; prioritize testing, debugging and a clean `main`.
- 2026-10-07: street + borough is the default search (exact address optional) and a read-only Resources tab is approved. Both shipped in PR #53. Community Q&A and the scaling fixes are on hold. The community board waits for a layout (posting 18+, no AI-written answers).
- 2026-10-11: `/law` index defaults approved: every section as number + title; City then State, one closed group per law; a number-only "Go to a section" box (no full-text search); repealed and dropped sections labelled, never hidden; linked from the section page and Resources, not the header nav.
- 2026-10-11: five languages. A fluent reader approves legal wording per language before it goes live; first visit follows the browser's language; sign-in emails stay English for now; Chinese ships as both Simplified and Traditional. Statutes, official data and anything a tenant or the assistant wrote are never translated.
- 2026-10-11: usage counts live in our own database, with no banner (Global Privacy Control is honoured) and 12 months of retention.
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
- Combined branch (`main` + phone drawer + translation foundation), 2026-10-11: **1115 OK (1 skipped)**. `main` at `b7d7bae` alone: 1081.
- Phone drawer: scripted in headless Chrome at 390 px and 1280 px (open, focus, Tab trap, Escape order, backdrop, resize handler, one chat from three taps). No screen reader, no real phone. Details: `log/2026-10-11-mobile-chat-drawer.txt`.
- Sign-in codes and password rule (merged): 987 OK on its own branch. Pages rendered in headless Chrome against an in-memory app. **No real email was sent**; mail delivery, the hourly limit and the email template are unverified. Details: `log/2026-10-11-email-codes-and-password-rule.txt`.
- "Your data" (`feat/your-data-button`): **930 OK (1 skipped)**. Rendered and scripted in headless Chrome against the in-memory app. No real model call was made, so extraction quality is untested. Details: `log/2026-10-11-your-data-button.txt`.
- `/law` index (merged): rendered locally with a faked library (light/dark, 1280 px and a 390 px frame). Live library counted read-only: 426 sections in 11 laws. Not yet opened on Render; keyboard use not driven by hand. Details: `log/2026-10-11-law-index.txt`.
- Workflow setup (2026-10-11): changes agent instructions, docs, two Python comments/docstrings and one workflow notice string; app logic is unchanged. Checks: the two Python files compile, the workflow YAML parses, no stale `docs/HANDOFF.md` references, `git diff --check` passes. The test suite was not run for it.

## Unresolved

- Owner items from the old checklist, not confirmed done: add GitHub secret `NYSENATE_API_KEY` (until then the quarterly refresh skips state law), and run the post-deploy checks on Render (sign-up with `password` is refused; one real `/building` lookup works).
- A brand-new database can't be built from `supabase/migrations/` alone: the original conversations/messages tables predate tracked migrations. Fixing this is separate engineering work; it would remove the need for live-DB migration tests.
- The live library holds Real Property Law `233-b*2` (Campgrounds), which the `/law/<citation>` number pattern rejects (404). The index lists it without a link. Owner decision: widen the pattern, or leave it.
- Not yet confirmed in a live session: that Claude Code loads `CLAUDE.md` and the imported `AGENTS.md` here (check once with `/memory` in Claude Code started in this folder).

## Next action

Owner steps left over from today's merges:
1. Sign-in codes (#60): confirm the project sends auth email through its own mail server and raise the hourly auth-email limit. Then, from Claude Desktop, one change at a time: the "Magic Link" template to a code email containing `{{ .Token }}`, code expiry at 1 hour or less, and the dashboard password rule. Then a live check with a real inbox.
2. Usage counts (#63): the migration has never been run. With a go-ahead for that run, test it in a rolled-back transaction, apply it, rename the file to the recorded version, run `supabase/checks/20261011_usage_counts_checks.sql` and the advisors. Until then nothing is recorded.
3. Live checks on Render: `/law` shows 426 sections; a real chat fills in "Your data"; a new chat opens with the greeting; "231 Ecco Place, Bronx" offers 231 Echo Place. On a real phone: the chat list opens as a drawer from the menu button, and rotating to landscape with it open leaves a normal page.
4. A lawyer's read of the privacy page wording.

Translation work, in order:
5. Wrap the remaining text (templates, JavaScript strings, JSON errors, messages raised in other modules), including the drawer's new labels, and fix the language menu's width at 390 px (the chat header now also holds the menu button and New chat).
6. One language at a time: fill the catalogue, have a fluent reader approve the `REVIEW` entries, compile, add it to `LIVE_LANGUAGES`.
7. Chat: reply-language tiebreaker and choices, the "quote the law in English" rule, the "not a translation" note on `/law`, the RA-81 character check.
