"""Local authentication primitives used by the Android/PRoot broker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re


USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
PASSWORD_MAX = 512
PASSWORD_MIN = 8


class AuthError(ValueError):
    pass


def validate_username(username: str) -> str:
    candidate = str(username or "").strip().lower()
    if not USERNAME_RE.fullmatch(candidate):
        raise AuthError("invalid username")
    return candidate


def validate_password(password: str) -> str:
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AuthError("password must contain 8 to 512 characters")
    return password


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    password = validate_password(password)
    salt = salt or os.urandom(16)
    try:
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode("ascii") + "$" + base64.urlsafe_b64encode(derived).decode("ascii")
    except (AttributeError, ValueError):
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000, dklen=32)
        return "pbkdf2-sha256$310000$" + base64.urlsafe_b64encode(salt).decode("ascii") + "$" + base64.urlsafe_b64encode(derived).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > PASSWORD_MAX or not isinstance(encoded, str):
        return False
    try:
        parts = encoded.split("$")
        if parts[0] == "scrypt" and len(parts) == 6:
            _name, n, r, p, salt_text, digest = parts[0], int(parts[1]), int(parts[2]), int(parts[3]), parts[4], parts[5]
            candidate = hashlib.scrypt(password.encode("utf-8"), salt=base64.urlsafe_b64decode(salt_text.encode("ascii")), n=n, r=r, p=p, dklen=32)
        elif parts[0] == "pbkdf2-sha256" and len(parts) == 4:
            _name, rounds, salt_text, digest = parts
            candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.urlsafe_b64decode(salt_text.encode("ascii")), int(rounds), dklen=32)
        else:
            return False
        return hmac.compare_digest(candidate, base64.urlsafe_b64decode(digest.encode("ascii")))
    except (ValueError, TypeError, IndexError, UnicodeError):
        return False


def default_permissions() -> dict[str, bool]:
    return {"desktop_access": True, "desktop_app_install": True, "shared_access": True, "drive": True, "photos": True, "files": True}


__all__ = ["AuthError", "default_permissions", "hash_password", "validate_password", "validate_username", "verify_password"]
