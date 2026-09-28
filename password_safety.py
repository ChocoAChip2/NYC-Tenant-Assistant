"""Refuses passwords that are known to be in public breach corpora.

WHY THIS IS HERE AND NOT A SUPABASE SETTING
Supabase Auth has exactly this feature built in, gated behind a paid plan.
This does the same job for free, and better in two ways: it runs on our own
signup / reset / change paths so we control the wording, and it is visible
in this repository where it can be read and tested rather than being a
checkbox in someone's dashboard.

HOW IT PROTECTS THE PASSWORD IT IS CHECKING
Have I Been Pwned's range API uses k-anonymity. We SHA-1 the password, send
only the FIRST FIVE hex characters of that hash, and get back every hash
SUFFIX in the corpus that starts with those five characters -- typically
several hundred of them -- each with a count. The comparison happens here,
locally.

So the password never leaves this process, and HIBP cannot tell which of
the ~800 candidates sharing that prefix we were asking about. The SHA-1 is
not protecting the password at rest; the prefix truncation is. (Supabase,
not this code, stores the real password hash, with bcrypt.)

The API is free, needs no key, and documents no rate limit.

TWO DELIBERATE SOFTENINGS

1. IT FAILS OPEN. If HIBP is slow, unreachable, or returns something
   unexpected, the password is allowed through. A tenant who cannot make an
   account because a third-party API blinked is a worse outcome than a weak
   password on an account that also has rate limiting and an
   exponentially-escalating login lockout in front of it. This is a
   defence-in-depth control, not a gate.

2. IT ONLY BLOCKS THE GENUINELY COMMON. A password seen once in a decade of
   breaches is a different risk from one seen four million times. Blocking
   everything that has ever appeared teaches people to fight the form;
   blocking what credential-stuffing lists actually contain stops the
   attack that actually happens. BREACH_BLOCK_THRESHOLD draws that line.
"""

from __future__ import annotations

import hashlib
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

RANGE_API = "https://api.pwnedpasswords.com/range/"

# Short on purpose: this sits in the request path of a signup. Better to
# let a password through than to make someone wait.
REQUEST_TIMEOUT_SECONDS = 3.0

# Appearances in the breach corpus at or above which we refuse the
# password. 100 is comfortably inside "on every credential-stuffing list"
# while leaving genuinely obscure reused passwords alone.
BREACH_BLOCK_THRESHOLD = 100

MESSAGE = (
    "That password has appeared in public data breaches, so attackers already "
    "have it on their lists. Please choose a different one."
)


def is_enabled() -> bool:
    """On by default. PASSWORD_BREACH_CHECK=off disables it, which exists so
    a deployment with no outbound network access can still run signups."""
    return os.environ.get("PASSWORD_BREACH_CHECK", "").strip().lower() not in {"off", "0", "false", "no"}


def _fetch_range(prefix: str) -> str:
    request = urllib.request.Request(
        RANGE_API + prefix,
        headers={
            # HIBP asks callers to identify themselves. Padding asks for
            # decoy records so the RESPONSE SIZE does not hint at how many
            # real matches the prefix had.
            "User-Agent": "SideKick-Tidbit-password-check",
            "Add-Padding": "true",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def times_breached(password: str) -> int | None:
    """How many times this password appears in the corpus.

    Returns 0 for "not found", a positive count when found, and None when
    the answer is unknown -- unreachable API, timeout, malformed response.
    None is not zero, and callers must not treat it as a pass/fail signal;
    see is_breached().
    """
    if not password or not is_enabled():
        return None

    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]

    try:
        body = _fetch_range(prefix)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Deliberately not logger.exception: an unreachable third party is
        # an expected condition here, not a bug, and this path can be hot.
        logger.warning("Password breach check unavailable (%s); allowing the password.", exc)
        return None
    except Exception:
        logger.exception("Unexpected failure in the password breach check; allowing the password.")
        return None

    for line in body.splitlines():
        candidate, _, count = line.partition(":")
        if candidate.strip().upper() == suffix:
            try:
                return int(count.strip())
            except ValueError:
                # A padded/decoy line, or a malformed one. Either way this
                # is not a usable answer.
                return None
    return 0


def is_breached(password: str) -> bool:
    """True only when we KNOW the password is widely breached.

    Unknown counts as fine. That is the fail-open rule, in one place, so no
    caller can get it wrong by testing `times_breached(...) > 0` against a
    None.
    """
    count = times_breached(password)
    return count is not None and count >= BREACH_BLOCK_THRESHOLD
