# NYC Tenant Assistant

NYC Tenant Assistant is a Flask web app with Supabase authentication and Gemini chat.  
Users sign up and log in first, then access the protected chat page.

## Render + Supabase confirmation

Render and Supabase work together in this project when these are set correctly:

- `SUPABASE_URL` (from your Supabase project settings)
- `SUPABASE_KEY` (usually the Supabase anon key for this flow)
- `GEMINI_API_KEY` (used to generate chat responses)
- `FLASK_SECRET_KEY` (any strong random string)

Optionally `GEMINI_API_KEY_2` through `GEMINI_API_KEY_10`, or a comma-separated
list in `GEMINI_API_KEY`, to give the quota fallback somewhere to fall back to.
Note that keys minted inside the *same* Google Cloud project share one quota
pool, so extra keys only add capacity if they come from separate projects.

Render start command:

```bash
python app.py
```

Optional (if you install Gunicorn): `gunicorn wsgi:app`

If env vars are missing, the app starts but auth actions fail with clear messages.

## Project structure (file purpose and relationships)

```text
.
├── app.py                # Application factory + Flask app bootstrap
├── ai_service.py         # Gemini client setup + response generation
├── config.py             # Environment configuration loader
├── supabase_service.py   # Supabase client setup + auth service methods
├── routes.py             # Signup/login/chat/logout HTTP routes (uses services)
├── wsgi.py               # WSGI entrypoint for Render/Gunicorn (imports app)
├── test.py               # Backward-compatible legacy entrypoint (imports app)
├── requirements.txt      # Python dependencies
├── templates/
│   ├── signup.html       # Signup page
│   ├── login.html        # Login page
│   └── chat.html         # Conversation sidebar + chat UI
├── tests/                # Unit tests (python -m unittest discover -s tests)
└── log/                  # Dated deployment summaries
```

## Model fallback

`ai_service.py` walks a chain of (model, API key) pairs so one retirement or one
exhausted key cannot stop a reply:

- **404 / NOT_FOUND** — the model is gone, so it is abandoned for every key.
- **429 / RESOURCE_EXHAUSTED** — that key is spent, so the next key is tried,
  then the next model. Free-tier limits apply per model as well as per key, so
  moving down the chain helps even with a single key.
- **5xx** — retried in place with exponential backoff.

All of this is invisible to the user: `routes.py` gets text or one error.
Update `FALLBACK_MODELS` when Google retires a model.

### How files connect

1. `app.py` calls `load_settings()` from `config.py`.
2. `app.py` creates `SupabaseService` from `supabase_service.py`.
3. `app.py` creates `AIService` from `ai_service.py`.
4. `app.py` stores shared services in app config and registers routes from `routes.py`.
5. `routes.py` handles auth and chat requests, rendering templates from `templates/`.
6. `wsgi.py` exposes the Flask app object for Render/Gunicorn.

## System prompt

`prompts.py` holds `SYSTEM_PROMPT`, which `AIService.generate_reply()` sends with
every chat request. It is applied unconditionally rather than passed in by the
caller, so no code path can accidentally answer a tenant as a generic chatbot.
Any `system` messages in the conversation history are appended after it.

The prompt sets the assistant's scope (NYC residential tenancy), requires it to
state that it is not a lawyer, and tells it to escalate urgent situations — no
heat, lockouts, court dates — toward real help.

Its accuracy rules are written for the app **as it is today**, with no retrieval
layer: the model is forbidden from citing statutes, section numbers, deadlines,
or contact details, because anything it recalls from memory may be wrong and a
bad citation can cost a tenant their case. **When law retrieval ships, that
section must be rewritten** to allow citing retrieved text.

Conversation titles do not carry this prompt — titling is a summarization task
with its own short instruction.

## Conversation naming

A conversation created without a title is named automatically from its first
exchange: after the opening reply is stored, `ai_service.generate_title()` asks
the cheapest model in the chain for a four-word summary, and `routes.py` saves
it. The naming call is fire-and-forget — if it fails the chat is unaffected and
the placeholder title stays. Titles the user typed themselves are never
overwritten.

## Local run

```bash
pip install -r requirements.txt
export SUPABASE_URL="..."
export SUPABASE_KEY="..."
export GEMINI_API_KEY="..."
export FLASK_SECRET_KEY="..."
python app.py
```
