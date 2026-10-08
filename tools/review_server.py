"""Local-only browser QA with disposable memory data and no external services.

Run: venv/bin/python -m tools.review_server
Open http://127.0.0.1:5057/login and use any example.invalid email/password.
Chat 'fail once' simulates one upstream failure; 'test pdf' fills a fixture PDF.
This module is never imported by the application entrypoint.
"""

import os
import secrets

if __name__ == "__main__":
    for name in ("SUPABASE_URL", "SUPABASE_KEY", "GEMINI_API_KEY", "ALERT_WEBHOOK_URL",
                 "DATA_ENCRYPTION_KEYS", "DATA_ENCRYPTION_ACTIVE_KEY_ID", "RENDER"):
        os.environ.pop(name, None)
    os.environ.update(FLASK_SECRET_KEY=secrets.token_hex(32), PASSWORD_BREACH_CHECK="off", LEGAL_CORPUS_ENABLED="0")
    from app import app
    from tests.review_support import MemorySupabase, ReviewAI
    app.config.update(SUPABASE_SERVICE=MemorySupabase(), AI_SERVICE=ReviewAI(), RATELIMIT_ENABLED=False)
    app.run(host="127.0.0.1", port=5057, use_reloader=False)
