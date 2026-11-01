"""The public domain shows a "coming soon" page until launch.

WHY (owner, 2026-11-01)
The site is on sidekicktidbit.com, but it isn't launched. Visitors to the
domain see one page: what the site will do, and a box to leave an email
address to hear when it opens. The team keeps working on the Render
address, which is unlisted and asks search engines not to index it.

HOW TO OPEN THE SITE
Set SITE_LAUNCHED=1 in Render. Nothing else changes: the gate only ever
applies to the hosts in PUBLIC_HOSTS, and only while that is unset.

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

import crypto_service

PUBLIC_HOSTS = frozenset({"sidekicktidbit.com", "www.sidekicktidbit.com"})
MAX_EMAIL_LENGTH = 254
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_HASH_CONTEXT = b"sidekick-tidbit launch list v1"


class NotStored(RuntimeError):
    """The address couldn't be kept as promised, so it wasn't kept."""


def is_launched() -> bool:
    return os.environ.get("SITE_LAUNCHED", "").strip().lower() in {"1", "true", "yes", "on"}


def _hostname(host: str | None) -> str:
    return (host or "").strip().lower().rsplit(":", 1)[0].rstrip(".")


def gated(host: str | None) -> bool:
    """Does a request to this host get the coming-soon page?"""
    return _hostname(host) in PUBLIC_HOSTS and not is_launched()


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
