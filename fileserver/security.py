"""Authentication helpers (HTTP Basic) with constant-time comparisons."""

import base64
import hmac
import secrets


def generate_password(length=12):
    """Return a URL-safe random password suitable for LAN use."""
    return secrets.token_urlsafe(length)


class Auth:
    """HTTP Basic authentication gate.

    When ``password`` is falsy authentication is disabled entirely.
    """

    def __init__(self, username="user", password=None):
        self.username = username or "user"
        self.password = password
        self.enabled = bool(password)

    def challenge(self):
        return 'Basic realm="local-file-webserver", charset="UTF-8"'

    def verify_header(self, header):
        if not self.enabled:
            return True
        if not header or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True)
            decoded = decoded.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        username, sep, password = decoded.partition(":")
        if not sep:
            return False
        ok_user = hmac.compare_digest(username, self.username)
        ok_pass = hmac.compare_digest(password, self.password)
        return ok_user and ok_pass