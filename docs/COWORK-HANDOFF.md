# Cowork Handoff

Cold-start brief for a session with no prior context. Read
[`BRANCH-PLAN.md`](./BRANCH-PLAN.md) alongside this.

---

## The project

**NYC Tenant Assistant** — a Flask web app that helps NYC tenants with housing
questions. Users sign up and log in through Supabase, then chat with a
Gemini-backed assistant. Conversations persist per user.

Repo: `ChocoAChip2/NYC-Tenant-Assistant` · Deployed on Render · Postgres and
auth on Supabase (project "Project Paradigm").

```
app.py               Flask app factory; wires services into app.config
config.py            Environment loader (Settings dataclass)
ai_service.py        Gemini client, model/key fallback chain, title generation
supabase_service.py  Auth + RLS-scoped data access
routes.py            signup / login / chat / conversations / logout
templates/           signup.html, login.html, chat.html
tests/               Unit tests (python -m unittest discover -s tests)
log/                 Dated deployment summaries — add one per shipped branch
docs/                BRANCH-PLAN.md, this file
```

### How a chat turn works

1. `POST /chat/message` with `{conversation_id, content}`.
2. Session token builds an RLS-scoped Supabase client — Postgres enforces
   per-user access, not application code.
3. User message is persisted.
4. **Full history is read back from Supabase** and sent to Gemini. The client's
   copy is never trusted, so a browser cannot spoof prior assistant turns.
5. Reply is persisted; `updated_at` is bumped; an unnamed conversation is named.

---

## Where things stand

**Working:** Supabase email/password auth, RLS-scoped persistence, named
conversations with history, Gemini chat with model/key fallback, automatic
conversation naming, 17 unit tests.

**In review:** PR #9 (`feat/ai/model-fallback-and-titles`).

**The biggest gap:** there is **no system prompt**. `ai_service.py` has
`system_instruction` plumbing, but nothing supplies one for the main chat. Today
this is a generic Gemini chatbot with a tenant-assistant title, with no domain
grounding and no "not legal advice" disclaimer anywhere. That is the next
branch and most other work depends on it.

---

## Rules for this repo

1. **One requirement per branch.** No unrelated cleanup, no drive-by
   refactors, no "while I was in there" changes.
2. **Respect the out-of-scope list.** Every branch entry in `BRANCH-PLAN.md`
   has one. If something outside it seems necessary, say so and ask — do not
   just do it.
3. **Naming:** `<type>/<domain>/<feature>`, e.g. `feat/ops/supabase-keepalive`.
   Types: `feat` `fix` `chore`. Domains: `ai` `chat` `data` `auth` `ops`.
4. **When work spans domains,** pick the domain that owns the *requirement*,
   not the one with the most changed lines. Split only when both halves ship
   independently. If one half is useless alone, keep them together.
5. **Tests for new logic.** `unittest`, no new dependencies. Never contact the
   real Gemini or Supabase API in a test — use fakes raising real error types.
6. **Match the surrounding style.** Comments explain *why*, not *what*. The
   codebase is heavily commented; keep that density.
7. **Add a `log/YYYY-MM-DD-<branch>.txt`** summary when a branch is ready,
   following the existing format. Include what was deliberately not done.
8. **Never commit secrets.** Keys come from environment variables only.

---

## Constraints

- **Free tier only.** All six models in `FALLBACK_MODELS` are free tier, as are
  `gemini-embedding-001` and Gemini Embedding 2.
- **One Google Cloud project.** Rate limits apply per *project*, not per API
  key, so extra keys add nothing until they come from separate projects. The
  code accepts `GEMINI_API_KEY_2..10` but only one is configured.
- **Supabase free tier pauses after 7 days of inactivity.** It has already
  happened once. Keep-alive cadence is **6 days**, leaving a day of margin.
- **Do not add dependencies** without flagging it first. `requirements.txt` is
  currently `supabase`, `Flask`, `google-genai`.

---

## Next task: `feat/ops/supabase-keepalive`

Self-contained. Shares no files with `feat/ai/system-prompt`, so both can run in
parallel.

**Requirement.** The Supabase project must not auto-pause, and we must find out
when the app breaks rather than discovering it by opening the tab.

**In scope**

- A GitHub Actions workflow on a **6-day** cron issuing a trivial authenticated
  query against Supabase, using repository secrets.
- Error alerting so a failed chat turn reaches a human. `routes.py` already
  calls `logger.exception(...)`; that output currently goes nowhere.
- Fix `FLASK_SECRET_KEY`, which defaults to `"change-me-in-render"` — a constant
  published in this public repo. It should fail loudly at startup instead. This
  rides along because it is a deployment-safety issue in the same files.
- Document the workflow and any new environment variables in `README.md`.

**Out of scope** — dashboards, tracing, full observability, uptime SLAs,
any change to chat or AI behavior.

**Done when**

- The workflow runs green on its schedule and the project stays unpaused.
- An induced error produces an alert a human actually receives.
- Starting the app without `FLASK_SECRET_KEY` fails with a clear message.

**Watch out for**

- GitHub Actions **disables scheduled workflows after 60 days of repo
  inactivity**, which would silently stop the keep-alive. Note it in the
  workflow file.
- A read-only query may not reset the inactivity timer. Verify what Supabase
  counts as activity rather than assuming a `SELECT` is enough.
- The Supabase project was auto-paused during recent work. It is running again
  and the schema has been verified; if a query fails, check the pause state
  before assuming the workflow is broken.

**Related, already known.** The Supabase security advisor reports that leaked
password protection is disabled — one dashboard toggle that checks new passwords
against HaveIBeenPwned. Not part of this task; listed in the `BRANCH-PLAN.md`
security backlog.
