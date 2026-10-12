"""Best-effort alerting so an unhandled error reaches a human.

routes.py already calls logger.exception(...) on failures (a broken chat
turn, a Supabase client that won't build, ...), but that output only ever
went to stdout/stderr -- fine for local dev, invisible in production unless
someone happens to be tailing Render logs at the moment it happens.

This attaches a logging.Handler that POSTs a short summary to a webhook URL
whenever something logs at ERROR level or above, which is exactly what
logger.exception() does. The payload shape ({"text": "..."}) is what Slack
incoming webhooks and Discord webhooks both expect out of the box; any other
endpoint can read the same field.

Deliberately built on stdlib only (urllib.request, logging) -- no new
dependency for something this small.
"""

import json
import logging
import os
import traceback
import urllib.request

# Keep a dead or slow webhook from ever blocking the request that triggered
# the alert.
_REQUEST_TIMEOUT_SECONDS = 5
# Chat webhooks truncate long messages anyway; keep alerts readable.
_MAX_ALERT_CHARS = 3500


class WebhookAlertHandler(logging.Handler):
    """Logging handler that POSTs ERROR-and-above records to a webhook URL."""

    def __init__(self, webhook_url: str):
        super().__init__(level=logging.ERROR)
        self.webhook_url = webhook_url

    def emit(self, record: logging.LogRecord) -> None:
        # logging.Handler's contract is that emit() must never raise: a dead
        # webhook or a network blip must not take down the request that
        # logged the original error, and must not recurse back into this
        # same handler by logging its own failure. self.handleError() is the
        # library-sanctioned escape hatch -- by default it prints to
        # stderr, which is exactly as visible as the pre-existing behavior.
        try:
            text = self.scrubbed(record)
            body = json.dumps({"text": f"NYC Tenant Assistant error:\n{text}"}).encode("utf-8")
            request = urllib.request.Request(
                self.webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS)
        except Exception:
            self.handleError(record)


    def scrubbed(self, record: logging.LogRecord) -> str:
        """The log line's template plus WHERE it failed, never the data.

        An exception's message can carry a request URL with an address in
        its query string, a database error quoting a row, or text from a
        chat. The webhook is a third-party service, so it gets the logger's
        message template (written by us, %s values left out), the exception
        TYPE and the file:line of each frame. The full record stays in the
        server log only. See docs/data-architecture-and-privacy.md section 9.
        """
        bare = logging.makeLogRecord(record.__dict__)
        # The message template only: values passed as %s arguments (an id,
        # a citation, or one day something a tenant typed) stay behind too.
        bare.msg, bare.args = str(record.msg), ()
        bare.exc_info = None
        bare.exc_text = None
        bare.stack_info = None
        text = self.format(bare)
        error = record.exc_info[1] if record.exc_info else None
        if error is not None:
            text += "\n" + exception_outline(error)
        return text[:_MAX_ALERT_CHARS]


def exception_outline(error: BaseException, max_frames: int = 8) -> str:
    """'ValueError (no message sent)' and its frames, for each exception in the chain."""
    lines, seen = [], set()
    while error is not None and id(error) not in seen and len(seen) < 5:
        seen.add(id(error))
        kind = type(error)
        name = kind.__name__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}"
        lines.append(f"{name} (message not sent)")
        for frame in traceback.extract_tb(error.__traceback__)[-max_frames:]:
            lines.append(f"  at {os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}")
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return "\n".join(lines)


def configure_alerting(alert_webhook_url: str | None) -> bool:
    """Attach the webhook handler to the root logger if a URL is configured.

    Returns whether alerting is active, so app.py can print a startup status
    line the same way it already does for Supabase/Gemini readiness.
    """

    if not alert_webhook_url:
        return False

    handler = WebhookAlertHandler(alert_webhook_url)
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))

    # Attached to the root logger, not a specific module logger: routes.py's
    # `logging.getLogger(__name__)` (and any future module logger) propagates
    # up to root by default, so this catches logger.exception() calls from
    # anywhere in the app without every call site needing to know alerting
    # exists.
    logging.getLogger().addHandler(handler)
    return True
