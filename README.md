# NYC Tenant Assistant

NYC Tenant Assistant is a Flask web app with Supabase authentication.  
Users sign up and log in first, then access the chat placeholder page.

## Render + Supabase confirmation

Render and Supabase work together in this project when these are set correctly:

- `SUPABASE_URL` (from your Supabase project settings)
- `SUPABASE_KEY` (usually the Supabase anon key for this flow)
- `FLASK_SECRET_KEY` (any strong random string)

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
├── config.py             # Environment configuration loader
├── supabase_service.py   # Supabase client setup + auth service methods
├── routes.py             # Signup/login/chat/logout HTTP routes (uses supabase_service)
├── wsgi.py               # WSGI entrypoint for Render/Gunicorn (imports app)
├── test.py               # Backward-compatible legacy entrypoint (imports app)
├── requirements.txt      # Python dependencies
└── templates/
    ├── signup.html       # Signup page
    ├── login.html        # Login page
    └── chat.html         # Post-login chat placeholder
```

### How files connect

1. `app.py` calls `load_settings()` from `config.py`.
2. `app.py` creates `SupabaseService` from `supabase_service.py`.
3. `app.py` stores that service in app config and registers routes from `routes.py`.
4. `routes.py` handles auth and chat requests, rendering templates from `templates/`.
5. `wsgi.py` exposes the Flask app object for Render/Gunicorn.

## Local run

```bash
pip install -r requirements.txt
export SUPABASE_URL="..."
export SUPABASE_KEY="..."
export FLASK_SECRET_KEY="..."
python app.py
```
