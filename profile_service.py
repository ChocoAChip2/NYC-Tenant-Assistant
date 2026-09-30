"""The sign-up profile: first name, last name and date of birth.

WHY IT IS STORED THE WAY IT IS
At sign-up there is no session yet (the tenant still has to confirm their
email), and the app deliberately holds no service-role key, so it cannot
write a row to a profiles table on the tenant's behalf. Supabase does let
sign-up attach metadata to the new account, so the profile goes there --
but ENCRYPTED first, as one AES-GCM envelope (crypto_service), so the
database, its backups and anyone with dashboard access see only
ciphertext. It lives on the account itself, so deleting the account (the
existing grace-period purge) deletes it too, with nothing left behind.

If encryption is not configured on a deployment, the profile is NOT
stored at all: the sign-up page promises it is kept encrypted, and that
promise is only kept by not writing plaintext. The age check still runs.

Accounts made before sign-up asked for a name can add one from Settings
("Your name"), which writes the same envelope through the tenant's own
session (supabase_service.update_profile).

WHAT IT IS USED FOR
The first name greets the tenant in chat (kept in the session, which is
the tenant's own browser). The date of birth confirms they are old enough
to use the service (MIN_AGE). Nothing else reads either field, and neither
is ever sent to the AI.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date

import crypto_service

logger = logging.getLogger(__name__)

MIN_AGE = 13  # the usual US floor (COPPA); teenagers often help family with housing problems
MAX_AGE = 120
MAX_NAME_LENGTH = 50
METADATA_KEY = "profile"

# Letters in any script, plus the joiners real names use: spaces, hyphens,
# apostrophes (straight and curly) and periods. No digits, no symbols.
_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '’\-.]+[^\W\d_]+)*\.?$")


@dataclass
class Profile:
    first_name: str
    last_name: str
    date_of_birth: date


class ProfileError(ValueError):
    """Shown to the tenant as-is, so every message is plain language."""


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def latest_allowed_birthday(today: date) -> date:
    """The latest date of birth that is still MIN_AGE today (for the form's max)."""
    try:
        return today.replace(year=today.year - MIN_AGE)
    except ValueError:  # today is Feb 29 and that year isn't a leap year
        return today.replace(year=today.year - MIN_AGE, day=28)


def _clean_name(raw: str, label: str) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise ProfileError(f"Please enter your {label}.")
    if len(name) > MAX_NAME_LENGTH:
        raise ProfileError(f"Your {label} can be at most {MAX_NAME_LENGTH} characters.")
    if not _NAME_RE.match(name):
        raise ProfileError(f"Your {label} can only contain letters, spaces, hyphens, apostrophes and periods.")
    return name


def validate(first_name: str, last_name: str, date_of_birth: str, *, today: date | None = None) -> Profile:
    today = today or date.today()
    first = _clean_name(first_name, "first name")
    last = _clean_name(last_name, "last name")
    try:
        born = date.fromisoformat((date_of_birth or "").strip())
    except ValueError:
        raise ProfileError("Please enter your date of birth.") from None
    if born > today:
        raise ProfileError("Your date of birth can't be in the future.")
    age = age_on(born, today)
    if age > MAX_AGE:
        raise ProfileError("Please check your date of birth.")
    if age < MIN_AGE:
        raise ProfileError(f"You must be at least {MIN_AGE} years old to create an account.")
    return Profile(first, last, born)


def to_metadata(profile: Profile) -> dict | None:
    """Account metadata holding the encrypted profile, or None if it can't be encrypted."""
    if not crypto_service.is_enabled():
        logger.warning("Encryption is not configured; the sign-up profile is not stored.")
        return None
    plaintext = json.dumps(
        {"first_name": profile.first_name, "last_name": profile.last_name,
         "date_of_birth": profile.date_of_birth.isoformat()},
        ensure_ascii=False,
    )
    envelope = crypto_service.encrypt(plaintext)
    if not crypto_service.is_encrypted(envelope):
        return None  # never store it in the clear
    return {METADATA_KEY: envelope}


def read_metadata(metadata) -> dict | None:
    """The decrypted profile from an account's metadata, or None (never raises).

    Returns {"first_name", "last_name", "date_of_birth"} as strings. Anything
    that isn't our encrypted envelope -- no profile, an older account, a
    value the user rewrote through the auth API -- reads as None.
    """
    if not isinstance(metadata, dict):
        return None
    try:
        envelope = metadata.get(METADATA_KEY)
        if not crypto_service.is_encrypted(envelope):
            return None
        data = json.loads(crypto_service.decrypt(envelope))
        if not isinstance(data, dict):
            return None
        fields = {key: data.get(key) for key in ("first_name", "last_name", "date_of_birth")}
        if not all(isinstance(value, str) for value in fields.values()):
            return None
        return fields
    except Exception:  # noqa: BLE001 -- a greeting is never worth a failed page
        logger.warning("Could not read the account profile.", exc_info=True)
        return None


def first_name_from_metadata(metadata) -> str | None:
    """The first name from an account's metadata, or None (never raises)."""
    profile = read_metadata(metadata)
    name = profile["first_name"] if profile else None
    return name if name and name.strip() else None
