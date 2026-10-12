# SideKick Tidbit: current handoff

Updated: 2026-10-12 (America/New_York). Status is taken from git, `log/`, the live site and the dashboards, as recorded under Verification.
Location: `~/Desktop/Github/nyc-tenant-assistant-push-2`, branch `main`. PRs up to #69 are merged and `6fc29f7` (PR #67) was the deployed commit on Render when the 2026-10-11 diagnostic pass ran; its fixes are in `log/2026-10-11-diagnostic-pass.txt`.

## Diagnostic pass, 2026-10-11 (owner: fixes allowed; code only)

- **Fixed in code** (see the log): real addresses on spelled-out ordinal streets ("400 First Avenue", "350 Fifth Avenue") were 404s; focus did not move into the phone chat drawer; `citation_guard` WARNING lines quoted the reply; webhook alerts carried exception text; the gunicorn access log kept `?address=` query strings and Referers; Settings offered sign-in codes that can't be delivered yet (now behind `SIGN_IN_CODES_ENABLED`); login fields lacked autocomplete hints.
- **Owner decisions/actions, one named change at a time** (nothing here was changed by the pass):
  1. Supabase sends auth email with the **built-in sender** (custom SMTP off). It only delivers to the project team, 2 per hour, so sign-up confirmation and password-reset emails cannot reach real tenants while "Confirm email" is on. Set up a mail server first; everything email-based depends on it.
  2. Supabase password rule is **6 characters, letters and digits**; the app requires 8 with lower, upper, digit and symbol. Raise the dashboard rule so the auth API refuses weak passwords too.
  3. The **GitHub** sign-in provider is enabled in Supabase; the app has no GitHub sign-in. Disable it unless it is wanted.
  4. "Magic link or OTP" template is still the default link email (no `{{ .Token }}`); OTP expiry is 3600 s.
  5. Sign-in codes switch lives in `user_metadata`, which a password-only session can change through the auth API (see `docs/frontend/auth-and-settings-pages.md`). Needs a small database change before codes are offered.
  6. `record_usage` errors (whole batch lost) on a row missing its `event` or `dim` key, because the NULL checks fall through. The app always sends both keys. A one-line `coalesce` fix needs a migration.

## Objective

Make the site readable in English, Spanish, Simplified Chinese, Swahili and French (owner, 2026-10-11). In slices, each with English unchanged. The first slice (foundation) is merged; see `docs/frontend/translations.md` for what is and isn't done.

Done when: translated pages show the machine-translation notice and the English originals of high-stakes wording, the four catalogues are filled and compiled, the chat pieces are in, and `es`, `fr`, `sw`, `zh_Hans` are added to `i18n.LIVE_LANGUAGES`. All page text is wrapped as of `feat/i18n-wrap-text`.

Merged on 2026-10-11: PRs #57 to #63 (workflow setup, `/law` index, "Your data", sign-in codes and password rule, new-chat greeting, "Did you mean", usage counts), then the phone chat drawer (PR #64, written with Codex) and the translation foundation (PR #65), combined and checked together on `integrate/drawer-and-i18n`. Several of them still have owner steps open; see Next action.

Waiting, not started: emailed PDF summary of a conversation. It needs a sending domain and email provider, and the owner to lift the feature hold for it.

## Decisions in force

- Audience is individual tenants first.
- 2026-10-01: hold new features; prioritize testing, debugging and a clean `main`.
- 2026-10-07: street + borough is the default search (exact address optional) and a read-only Resources tab is approved. Both shipped in PR #53. Community Q&A and the scaling fixes are on hold. The community board waits for a layout (posting 18+, no AI-written answers).
- 2026-10-11: `/law` index defaults approved: every section as number + title; City then State, one closed group per law; a number-only "Go to a section" box (no full-text search); repealed and dropped sections labelled, never hidden; linked from the section page and Resources, not the header nav.
- 2026-10-11 (later the same day, replacing the first answers): translations are machine-made with no fluent-reader gate; English stays the reference, so translated pages say so and link to English, and high-stakes wording shows its English original. Chinese is Simplified only. First visit follows the browser's language. Sign-in, confirmation, reset and code emails stay English. Statutes, official data and anything a tenant or the assistant wrote are never translated.
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

- `NYSENATE_API_KEY` is set as a GitHub secret (seen masked in the 2026-10-01 and 2026-10-07 workflow logs). One real `/building` lookup works on Render (2026-10-11). A sign-up with a weak password on Render was not submitted.
- A brand-new database can't be built from `supabase/migrations/` alone: the original conversations/messages tables predate tracked migrations. Fixing this is separate engineering work; it would remove the need for live-DB migration tests.
- The live library holds Real Property Law `233-b*2` (Campgrounds), which the `/law/<citation>` number pattern rejects (404). The index lists it without a link. Owner decision: widen the pattern, or leave it.
- Not yet confirmed in a live session: that Claude Code loads `CLAUDE.md` and the imported `AGENTS.md` here (check once with `/memory` in Claude Code started in this folder).

## Next action

Owner steps left over from today's merges:
1. Sign-in codes (#60): confirm the project sends auth email through its own mail server and raise the hourly auth-email limit. Then, from Claude Desktop, one change at a time: the "Magic Link" template to a code email containing `{{ .Token }}`, code expiry at 1 hour or less, and the dashboard password rule. Then a live check with a real inbox.
2. Usage counts (#63): the migration **is applied** (live history has `20261011212103 usage_daily_counts`, matching the file) and counts were recorded on 2026-10-11; the purge job is scheduled. Its log and the file's header still say "not applied": historical. Left: the `record_usage` NULL-key fix above.
3. Live checks on Render, 2026-10-11: `/law` shows 426 sections in 11 laws (425 linked; `233-b*2` unlinked as known); "231 Ecco Place, Bronx" is a 404 offering 231 Echo Place with no Brown Place; signed-out and signed-in logo links go to `/` and `/chat`. Still open: a real chat filling in "Your data", the greeting on a real account, and the drawer on a real phone.
4. A lawyer's read of the privacy page wording.

Translation work, in order (steps 1 and 2, wrapping the text and the English-reference display, are done):
6. Fill `es`, `fr`, `sw` and `zh_Hans` by machine translation with one consistent tool, check nothing is empty or fuzzy, compile, commit the compiled files.
7. Chat: add Kiswahili and Français to the reply-language choices (keep `中文` valid, meaning Simplified); interface language as the tiebreaker; non-English replies quote the law in English; a note on `/law`; link HCR Fact Sheet #14 on Spanish and Chinese pages.
8. Go live: add the four languages to `LIVE_LANGUAGES` together, then the owner's live chat check in each language.
