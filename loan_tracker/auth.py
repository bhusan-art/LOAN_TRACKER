"""Owner login: scrypt password hashes + TOTP 2FA (RFC 6238), stdlib only.

TOTP works with Google Authenticator, Microsoft Authenticator, Aegis, etc.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("use a password of at least 10 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify_totp(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    """Accept the current 30-second code and one step either side (clock drift)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    now = time.time() if at is None else at
    return any(hmac.compare_digest(totp_code(secret, now + k * 30), code) for k in range(-window, window + 1))


def otpauth_uri(secret: str, username: str, issuer: str = "Loan Tracker") -> str:
    """Paste or turn into a QR code to add the account to an authenticator app."""
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}"
            f"?secret={secret}&issuer={quote(issuer)}&digits=6&period=30")
