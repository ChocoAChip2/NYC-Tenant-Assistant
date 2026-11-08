"""The public domain shows a "coming soon" page until launch.

WHY (owner, 2026-11-01)
The site is on sidekicktidbit.com, but it isn't launched. Visitors to the
domain see one page: what the site will do, and a box to leave an email
address to hear when it opens. The team keeps working on the Render
address, which is unlisted and asks search engines not to index it.

OPEN AGAIN (owner, 2026-11-08)
"Let's just open the whole site so we can work on it." The domain now
serves the real site by default. COMING_SOON=1 in Render closes it again
(the coming-soon page, launch list and team preview all still work then).
Until SITE_LAUNCHED=1, every page on the domain carries
X-Robots-Tag: noindex, so search engines don't list a site that hasn't
launched; SITE_LAUNCHED=1 removes that and overrides COMING_SOON.

TEAM PREVIEW (owner, 2026-11-08)
The team can use the real site on the domain before launch. Set
TEAM_PREVIEW_KEY in Render (20+ characters, shared privately), open
/team-preview, enter it once: the browser gets a signed cookie for
PREVIEW_DAYS and skips the gate. The cookie holds a fingerprint of the
key, never the key, so changing or removing the key in Render ends every
preview at once. It is separate from the sign-in session, so signing out
doesn't end it; /team-preview/leave does.

THE LAUNCH LIST
An address is stored encrypted (crypto_service) next to a keyed hash of
it, which is what stops the same address being added twice without
storing it in the clear. The only way in is the join_launch_list()
database function (supabase/migrations/*_launch_list.sql): the app's
public key can add an address and can't read any back. The list is read
from the SQL editor and decrypted with tools/export_launch_list.py when
it's time to send the launch email, then deleted.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from itsdangerous import BadSignature, URLSafeTimedSerializer

import crypto_service

PUBLIC_HOSTS = frozenset({"sidekicktidbit.com", "www.sidekicktidbit.com"})
MAX_EMAIL_LENGTH = 254
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_HASH_CONTEXT = b"sidekick-tidbit launch list v1"


PREVIEW_COOKIE = "team_preview"
PREVIEW_DAYS = 30
MIN_PREVIEW_KEY_LENGTH = 20


class NotStored(RuntimeError):
    """The address couldn't be kept as promised, so it wasn't kept."""


def is_launched() -> bool:
    return os.environ.get("SITE_LAUNCHED", "").strip().lower() in {"1", "true", "yes", "on"}


def _hostname(host: str | None) -> str:
    return (host or "").strip().lower().rsplit(":", 1)[0].rstrip(".")


def is_closed() -> bool:
    """Is the domain showing the coming-soon page? Off unless COMING_SOON is set."""
    if is_launched():
        return False
    return os.environ.get("COMING_SOON", "").strip().lower() in {"1", "true", "yes", "on"}


def is_public_host(host: str | None) -> bool:
    return _hostname(host) in PUBLIC_HOSTS


def gated(host: str | None) -> bool:
    """Does a request to this host get the coming-soon page?"""
    return is_public_host(host) and is_closed()


def hide_from_search(host: str | None) -> bool:
    """The team's Render address always; the domain until launch."""
    return is_team_host(host) or (is_public_host(host) and not is_launched())


def is_team_host(host: str | None) -> bool:
    """The Render address the team works on: kept out of search engines."""
    return _hostname(host).endswith(".onrender.com")


def clean_email(raw: str | None) -> str | None:
    """A plausible address, trimmed and lower-cased; None if it isn't one."""
    email = (raw or "").strip().lower()
    if not email or len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(email):
        return None
    return email


def email_key(email: str) -> str:
    """A hash that's the same for the same address, and useless without the app's key."""
    key = crypto_service.hash_key()
    if key is None:
        return hashlib.sha256(_HASH_CONTEXT + email.encode()).hexdigest()
    return hmac.new(hmac.new(key, _HASH_CONTEXT, hashlib.sha256).digest(), email.encode(), hashlib.sha256).hexdigest()


def join(client, email: str, language: str | None) -> None:
    """Add an address to the launch list. Joining twice is not an error.

    Raises NotStored when encryption isn't configured (the page promises
    the address is encrypted, so there is no plaintext fallback, as with
    the sign-up profile), and whatever the database call raises; the route
    turns either into a "try again" message.
    """
    if not crypto_service.is_enabled():
        raise NotStored("Encryption is not configured.")
    client.rpc("join_launch_list", {
        "p_email_key": email_key(email),
        "p_email": crypto_service.encrypt(email),
        "p_language": (language or "en")[:10],
    }).execute()


def preview_key() -> str | None:
    """The team's key from Render, or None (preview off) if unset or too short."""
    key = os.environ.get("TEAM_PREVIEW_KEY", "").strip()
    return key if len(key) >= MIN_PREVIEW_KEY_LENGTH else None


def key_matches(given: str | None) -> bool:
    key = preview_key()
    return bool(key) and hmac.compare_digest((given or "").strip().encode(), key.encode())


def _fingerprint(key: str) -> str:
    return hashlib.sha256(b"sidekick-tidbit team preview:" + key.encode()).hexdigest()[:32]


def _serializer(secret) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="team-preview")


def preview_cookie_value(secret) -> str:
    """What goes in the cookie after a correct key: signed, and not the key."""
    return _serializer(secret).dumps(_fingerprint(preview_key() or ""))


def in_preview(cookie: str | None, secret) -> bool:
    key = preview_key()
    if not key or not cookie or not secret:
        return False
    try:
        fingerprint = _serializer(secret).loads(cookie, max_age=PREVIEW_DAYS * 24 * 3600)
    except BadSignature:
        return False
    return isinstance(fingerprint, str) and hmac.compare_digest(fingerprint, _fingerprint(key))
