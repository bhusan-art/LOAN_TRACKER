"""Encrypted document vault (Phase 14). Pure: no database, no disk, no clock.

AES-256-GCM (cryptography library). Each file is sealed with a fresh 12-byte
random nonce and bound to its document id through the associated data, so a
file copied onto another document's name fails to open. Stored format:

    b"LTD1" + key_id (8 bytes) + nonce (12 bytes) + ciphertext+tag

The key lives in .env as DOCUMENT_KEY (32 random bytes, base64), apart from the
database: a database backup alone cannot open the files, and LOSING THE KEY
LOSES THE DOCUMENTS. DOCUMENT_KEY_OLD may hold the previous key, read-only,
after a key change.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"LTD1"
MAX_DOC_BYTES = 10 * 1024 * 1024
KINDS = ("ID_PROOF", "AGREEMENT", "CHEQUE", "PHOTO", "OTHER")


class VaultError(Exception):
    """A document cannot be opened (missing key, damaged or swapped file)."""


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def parse_key(text: str) -> bytes:
    """Base64 (standard or URL-safe, padding optional) of exactly 32 bytes."""
    t = (text or "").strip()
    if not t:
        raise ValueError("DOCUMENT_KEY is not set")
    t += "=" * (-len(t) % 4)
    try:
        raw = base64.b64decode(t.replace("-", "+").replace("_", "/"), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("DOCUMENT_KEY must be base64; create one with: python -m loan_tracker.cli gen-doc-key") \
            from None
    if len(raw) != 32:
        raise ValueError("DOCUMENT_KEY must be 32 bytes (44 base64 characters); create one with: "
                         "python -m loan_tracker.cli gen-doc-key")
    return raw


def key_id(key: bytes) -> str:
    """Public fingerprint of a key (never the key itself): which key sealed a file."""
    return hashlib.sha256(b"loan-tracker document key\x00" + key).hexdigest()[:16]


def seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + bytes.fromhex(key_id(key)) + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(keys: dict[str, bytes], blob: bytes, aad: bytes) -> bytes:
    """keys: key_id -> key (current first, then old ones)."""
    if len(blob) < 4 + 8 + 12 + 16 or not blob.startswith(MAGIC):
        raise VaultError("the stored file is damaged or not ours")
    kid = blob[4:12].hex()
    key = keys.get(kid)
    if key is None:
        raise VaultError(f"this document was sealed with another key ({kid}); set that key as DOCUMENT_KEY "
                         "or DOCUMENT_KEY_OLD")
    try:
        return AESGCM(key).decrypt(blob[12:24], blob[24:], aad)
    except InvalidTag:
        raise VaultError("the stored file is damaged or not ours") from None


def sniff(data: bytes) -> tuple[str, str] | None:
    """(content type, extension) from the file's first bytes; None if not an allowed type."""
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None
