"""Test package setup.

The suite must never touch the network. The HIBP breached-password check
(password_safety.py) is on by default and fails open, so on a machine with
no internet those tests passed by accident. On a machine WITH internet, real
HIBP answers turned test passwords like "newpassword123" into "breached"
and five route tests failed. Found running the suite on the owner's Mac,
2026-09-29.

tests/test_password_safety.py sets this variable explicitly wherever it
tests the check itself.
"""

import os

os.environ.setdefault("PASSWORD_BREACH_CHECK", "off")
