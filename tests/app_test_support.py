"""Makes a bare Flask test app behave like the one create_app() builds.

Most tests here construct `flask.Flask(...)` and register main_bp directly
rather than going through create_app(), because they want fake services.
That skips two things create_app() does, and skipping either one fails in
a quiet way:

- CSRFProtect registers the csrf_token() Jinja global. Without it, every
  template containing a form raises UndefinedError on render.
- branding.register() supplies the product name and the legal disclaimer.
  Without it those render as empty strings -- the page still comes out,
  just unbranded and with no fine print, and a test asserting on anything
  else stays green while the disclaimer silently vanishes.

CSRF enforcement is turned back off afterwards: these tests are not about
CSRF, and tests/test_security_hardening.py covers it through the real app.

The module is deliberately not named test_*.py so unittest discovery does
not treat it as a test module.
"""

from flask_wtf import CSRFProtect

import branding


def configure_test_app(app):
    """Register the template globals a bare test app would otherwise lack."""
    app.config["WTF_CSRF_ENABLED"] = False
    CSRFProtect(app)
    branding.register(app)
    return app
