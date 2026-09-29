"""Branded error pages, and JSON errors for the chat endpoint.

Before this module, a 404, a CSRF failure (400) or a rate limit (429) showed
Werkzeug's or Flask-Limiter's bare default page: unbranded, with no way back
into the site and without the legal disclaimer every page is supposed to
carry (docs/frontend/branding-and-disclaimers.md). Found by the 2026-09-29
browser sweep.

The chat page talks to /chat/message with fetch() and reads `{"error": ...}`
from the body, so JSON callers get JSON rather than an HTML page they
cannot show.
"""

from __future__ import annotations

import logging

from flask import jsonify, render_template, request
from werkzeug.exceptions import HTTPException, InternalServerError

logger = logging.getLogger(__name__)

# status -> (heading, message). Plain words, no blame, and a next step.
COPY = {
    400: ("Something about that request didn't work",
          "The page may have been open for a long time, or a form was sent twice. Go back, reload the page and try again."),
    403: ("You don't have access to that page", "If you think you should, log in again and retry."),
    404: ("We couldn't find that page", "The link may be old or mistyped."),
    405: ("That page can't be used that way", "Go back and use the page's own buttons and forms."),
    413: ("That was too much to send at once", "Try again with a shorter message."),
    429: ("You're going a little fast", "Too many requests in a short time. Wait a minute, then try again."),
    500: ("Something went wrong on our side", "It has been logged. Please try again in a moment."),
}
_FALLBACK = ("Something went wrong", "Please go back and try again.")


def _wants_json() -> bool:
    return request.path.startswith("/chat/message") or request.is_json


def _render(status: int, description: str | None = None):
    heading, message = COPY.get(status, _FALLBACK)
    if _wants_json():
        return jsonify({"error": f"{heading}. {message}"}), status
    return render_template("error.html", status=status, heading=heading, message=message), status


def register(app) -> None:
    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        # Redirects and other non-errors pass straight through.
        if exc.code is None or exc.code < 400:
            return exc
        response, status = _render(exc.code)
        response = app.make_response((response, status))
        # Keep the headers the exception carried: Allow on a 405, and
        # Retry-After / X-RateLimit-* on a 429 (see RATELIMIT_HEADERS_ENABLED).
        for name, value in exc.get_headers():
            if name.lower() != "content-type":
                response.headers[name] = value
        return response

    @app.errorhandler(InternalServerError)
    def _server_error(exc: InternalServerError):
        return _render(500)
