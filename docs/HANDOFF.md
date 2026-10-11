# SideKick Tidbit: current handoff

Updated: 2026-10-11 (America/New_York). Status is taken from git, `log/` and the test runs recorded under Verification.
Location: `~/Desktop/Github/nyc-tenant-assistant-push-2`, branch `main` at `610f456` (PR #66, 2026-10-11).

## Logo-link handoff for Claude Code (2026-10-11)

- **Where to work:** `~/Desktop/Github/sidekick-logo-home-link`, branch
  `fix/logo-home-link`, based on `main` at `610f456` (PR #66). The files are
  already in the shared Github folder; no copy from Codex is needed.
  `~/Desktop/Github/INDEX.md` points here.
- **Objective and decision:** public-page logos go to `/chat` when the session
  has `user_id`, otherwise `/`. `/` remains street search. The one `home_url`
  context value in `routes.py` supplies all seven public templates.
- **Completed:** routes.py context value; logo hrefs in area, building,
  resources, law, law_index, learn_more and privacy; regressions in
  `tests/test_home_page.py`; change log and index. Chat/settings templates,
  page layouts and in-page search links are unchanged.
- **Verification:** 177 tests pass in the six requested modules; full suite
  1120 OK (1 skipped); diff whitespace check passes. The offline street-search
  journey follows the rendered logo to chat. Manual browser clicking and
  visual checks remain unverified because the saved browser access restriction
  from the prior task is unresolved.
- **Integration:** [PR #67](https://github.com/ChocoAChip2/NYC-Tenant-Assistant/pull/67)
  is open; branch `fix/logo-home-link` is pushed. Claude should check branch, HEAD,
  status and the diff against fresh main before reviewing/integrating. Do not
  discard unrelated work in the main clone. Finish the manual signed-in and
  signed-out logo checks when browser access is available. The owner merges;
  this task does not authorize an agent to merge.
- **Evidence:** `log/2026-10-11-logo-home-link.txt`. Earlier project work and
  remaining translation tasks are retained below.

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

Translation work, in order (steps 1 and 2, wrapping the text and the English-reference display, are done):
6. Fill `es`, `fr`, `sw` and `zh_Hans` by machine translation with one consistent tool, check nothing is empty or fuzzy, compile, commit the compiled files.
7. Chat: add Kiswahili and Français to the reply-language choices (keep `中文` valid, meaning Simplified); interface language as the tiebreaker; non-English replies quote the law in English; a note on `/law`; link HCR Fact Sheet #14 on Spanish and Chinese pages.
8. Go live: add the four languages to `LIVE_LANGUAGES` together, then the owner's live chat check in each language.
