"""Encrypted document vault (Phase 14). Pure: no database, no disk."""
import base64

import pytest

from loan_tracker.docvault import (MAX_DOC_BYTES, VaultError, key_id, new_key, open_sealed, parse_key, seal,
                                   sniff)

K1 = parse_key(new_key())
K2 = parse_key(new_key())
PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF"


def test_round_trip_and_fresh_nonce():
    a = seal(K1, PDF, b"doc:abc")
    b = seal(K1, PDF, b"doc:abc")
    assert a != b                                  # a new random nonce every time
    assert PDF not in a                            # nothing readable on disk
    assert open_sealed({key_id(K1): K1}, a, b"doc:abc") == PDF


def test_tampering_wrong_key_and_swapped_files_fail():
    blob = seal(K1, PDF, b"doc:abc")
    bad = bytearray(blob)
    bad[-1] ^= 1
    with pytest.raises(VaultError, match="damaged or not ours"):
        open_sealed({key_id(K1): K1}, bytes(bad), b"doc:abc")
    with pytest.raises(VaultError, match="key"):
        open_sealed({key_id(K2): K2}, blob, b"doc:abc")
    with pytest.raises(VaultError, match="damaged or not ours"):
        open_sealed({key_id(K1): K1}, blob, b"doc:other")   # file moved onto another document


def test_old_key_still_reads_after_rotation():
    blob = seal(K1, PDF, b"doc:abc")
    assert open_sealed({key_id(K2): K2, key_id(K1): K1}, blob, b"doc:abc") == PDF


def test_key_parsing():
    raw = bytes(range(32))
    for text in (base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode().rstrip("=")):
        assert parse_key(text) == raw
    for bad in ("", "short", base64.b64encode(b"x" * 16).decode(), "not base64 !!"):
        with pytest.raises(ValueError, match="DOCUMENT_KEY"):
            parse_key(bad)
    assert len(key_id(K1)) == 16 and key_id(K1) != key_id(K2)


@pytest.mark.parametrize("data, kind", [
    (PDF, ("application/pdf", "pdf")),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", ("image/jpeg", "jpg")),
    (b"\x89PNG\r\n\x1a\n\x00\x00", ("image/png", "png")),
    (b"RIFF\x24\x00\x00\x00WEBPVP8 ", ("image/webp", "webp")),
    (b"<html><script>alert(1)</script>", None),
    (b"MZ\x90\x00", None),                          # a Windows program renamed to .pdf
    (b"", None),
])
def test_type_is_decided_by_content(data, kind):
    assert sniff(data) == kind


def test_size_limit_is_ten_mb():
    assert MAX_DOC_BYTES == 10 * 1024 * 1024
