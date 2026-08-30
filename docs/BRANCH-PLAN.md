# Branch Plan

Working document for delegating feature work. One requirement per branch.
Nothing extra unless it directly serves that requirement.

## Naming convention

```
<type>/<domain>/<feature>
```

**Type** — `feat` (new capability), `fix` (bug), `chore` (deps, config, docs).

**Domain** — the part of the system that owns the change:

| Domain | Covers | Primary files |
|---|---|---|
| `ai` | Gemini calls, prompts, models, keys | `ai_service.py`, `config.py` |
| `chat` | Conversation UX and message flow | `routes.py`, `templates/chat.html` |
| `data` | Supabase schema, RLS, persistence | `supabase_service.py`, migrations |
| `auth` | Signup, login, sessions, tokens | `routes.py`, `templates/login.html` |
| `ops` | Deploy, monitoring, CI, keep-alive | `.github/workflows/`, `README.md` |

Examples: `feat/ai/system-prompt`, `fix/data/conversation-ordering`,
`chore/ops/pin-dependencies`.

**When work spans two domains,** pick the domain that owns the *requirement*,
not the one with the most changed lines. Split into two branches only when the
halves can ship and be reviewed independently. If one half is useless without
the other, they belong together — an artificial split just creates a broken
intermediate state on `main`.

---

## Current decisions

- **One Google Cloud project for now.** The multi-key code accepts
  `GEMINI_API_KEY_2..10` but with one key configured it simply has nothing to
  rotate to; model fallback still works. The 10-project workload split
  (chat / titles / embeddings / evals / dev) is deferred, not discarded —
  see `feat/ai/role-scoped-api-keys` below.
- **Supabase keep-alive fires on day 6.** The free tier pauses after 7 days of
  inactivity, so day 6 leaves a one-day safety margin.
- **Free tier only.** Every model in `FALLBACK_MODELS` is free tier, as are
  `gemini-embedding-001` and Gemini Embedding 2.

---

## Done — awaiting review

### `feat/ai/model-fallback-and-titles`

Keep replies flowing when a model is retired or a key hits its quota, and name
conversations automatically from their first exchange.

Two features in one branch because both rewrite `ai_service.generate_content`
call paths; splitting would have left an intermediate state where titles call a
method the fallback branch hadn't introduced yet.

- Fallback chain across all six non-EOL Flash/Flash-Lite models.
- 404 retires a model for every key; 429 advances key then model; 5xx retries
  with backoff. All invisible to the user.
- Auto-naming from the opening exchange, using the cheapest model in the chain.
- Schema verified live against Supabase after the project resumed.
- 17 unit tests. Gemini API never contacted.

---

## Planned

Ordered by dependency. `feat/ai/system-prompt` unblocks the two largest items,
so it goes first.

### 1. `feat/ai/system-prompt`

**Requirement.** The assistant must answer as a NYC tenant-rights tool, not a
generic chatbot, and must state that it is not a lawyer.

Today nothing is sent with a user's message. `ai_service` has
`system_instruction` plumbing that nothing fills in.

- **In scope:** the prompt constant; injecting it in `generate_reply`; a visible
  disclaimer in `chat.html`; tests asserting the instruction reaches the call.
- **Out of scope:** retrieval, citations, per-user prompt customization.
- **Files:** `ai_service.py`, `templates/chat.html`, `tests/`
- **Done when:** every chat call carries the instruction, an off-topic question
  is declined, and the disclaimer is visible without scrolling.
- **Depends on:** nothing.

### 2. `feat/ops/supabase-keepalive`

**Requirement.** The Supabase project must not auto-pause, and we must find out
when the app breaks instead of discovering it by opening the tab.

Free tier pauses after 7 days of inactivity. This already happened once.

- **In scope:** GitHub Actions cron on a 6-day cadence issuing a trivial
  authenticated query; error alerting on the Flask app.
- **Out of scope:** full observability, dashboards, tracing.
- **Files:** `.github/workflows/keepalive.yml`, `app.py`, `README.md`
- **Done when:** the workflow runs green on schedule and an induced error
  produces an alert.
- **Note:** GitHub Actions disables scheduled workflows after 60 days of repo
  inactivity. Document this in the workflow file.
- **Depends on:** nothing. Can run in parallel with any other branch.

### 3. `feat/chat/streaming-responses`

**Requirement.** Replies should appear as they are generated, with wording that
suits a tenant-help tool rather than a generic spinner.

- **In scope:** `generate_content_stream`; server-sent events from
  `/chat/message`; incremental rendering; tenant-specific pending copy; the
  assistant message persisted once the stream completes.
- **Out of scope:** stop/regenerate buttons, markdown rendering.
- **Files:** `ai_service.py`, `routes.py`, `templates/chat.html`
- **Done when:** text appears progressively and the full reply is persisted even
  if the browser disconnects mid-stream.
- **Depends on:** `feat/ai/system-prompt` (shares the `generate_reply` path;
  rebase after it merges to avoid a conflict).

### 4. `feat/data/usage-tracking`

**Requirement.** We need to see token consumption per user so a rate-limit
problem is visible before a user hits it.

- **In scope:** migration adding token columns to `messages`; capturing counts
  from the Gemini response; recording which model and key served each call.
- **Out of scope:** dashboards, quotas, billing, rate limiting itself.
- **Files:** `supabase_service.py`, `routes.py`, `ai_service.py`, migration
- **Done when:** every message row carries input/output token counts and the
  model that produced it.
- **Depends on:** `feat/chat/streaming-responses` if it merges first — streaming
  changes where the token count is read from.

### 5. `feat/ai/housing-law-retrieval`

**Requirement.** The assistant must cite real NYC housing law rather than
answering from model memory.

The largest item. Consider splitting once scoped — ingestion and query-time
retrieval may be separable.

- **In scope:** enable `pgvector`; schema for law chunks and embeddings;
  ingestion script; similarity search at query time; retrieved text injected
  into the prompt with citations.
- **Out of scope:** PDF form handling; sources beyond the first chosen corpus.
- **Files:** new module, `supabase_service.py`, `ai_service.py`, migration
- **Done when:** a question about a covered statute returns an answer quoting
  the retrieved text with a citation.
- **Open questions:** which corpus, how it stays current, re-ingest cadence.
- **Depends on:** `feat/ai/system-prompt`. Enable `pgvector` before production
  data accumulates.

### 6. `feat/ai/answer-evals`

**Requirement.** We must be able to tell whether a prompt or model change made
the legal answers worse.

Distinct from the unit tests, which prove the code works and say nothing about
answer quality.

- **In scope:** a fixture set of tenant questions with verified answers; a
  runner; a readable report.
- **Out of scope:** CI gating, automated scoring.
- **Files:** `evals/`
- **Done when:** one command reports pass/fail per question against the live app.
- **Depends on:** `feat/ai/system-prompt`. Far more useful after retrieval.

### 7. `feat/ai/role-scoped-api-keys`

**Requirement.** Separate quota pools per workload so evals or bulk ingestion
cannot starve live chat.

Deferred until one project's rate limits actually bind. Check
https://aistudio.google.com/rate-limit before starting.

- **In scope:** `GEMINI_CHAT_KEYS` / `GEMINI_TITLE_KEYS` / `GEMINI_EMBED_KEYS`,
  each falling back to `GEMINI_API_KEY`; per-role client pools.
- **Out of scope:** creating the projects, key rotation policy.
- **Files:** `config.py`, `ai_service.py`, `README.md`
- **Depends on:** nothing technically. Blocked on the decision to add projects.

---

## Security backlog

Known issues from the 2026-08-30 review, not yet scheduled. Each is small
enough to fold into a related branch rather than justify its own.

| Issue | Suggested home |
|---|---|
| `FLASK_SECRET_KEY` defaults to a constant in this public repo | `feat/ops/supabase-keepalive` |
| Supabase JWT stored in a signed-but-unencrypted session cookie | own branch, `fix/auth/session-token-storage` |
| No CSRF protection on `POST /conversations` | own branch, `fix/auth/csrf-protection` |
| Raw exception text flashed to users; enables account enumeration | `fix/auth/csrf-protection` |
| No rate limiting on `/chat/message` | `feat/data/usage-tracking` |
| No token refresh; expiry forces re-login | `fix/auth/session-token-storage` |
| Unpinned dependencies; dev server in production | `chore/ops/pin-dependencies` |
| Schema not version-controlled as migrations | `feat/data/usage-tracking` |
| Leaked password protection disabled (one dashboard toggle) | `fix/auth/csrf-protection` |
| `bump_conversation_updated_at` has a mutable `search_path` | `feat/data/usage-tracking` |
| `conversations` / `messages` discoverable via GraphQL to `anon` | `fix/auth/csrf-protection` |
