"""Oracle tests for environment/codebase/5 (AuthCore).

Bug: ``decode`` returns the payload for any token whose header has
``"alg": "none"`` before checking the signature, so an attacker can forge tokens
that are accepted as valid.

These tests import the real ``authcore`` package from the codebase, so they fail
while ``alg: none`` is trusted and pass once it is rejected.
"""

import json
import os
import sys
import time
import unittest


def _add_codebase_to_path(marker, number):
    """Put the directory containing ``marker`` for codebase ``number`` on sys.path."""
    candidates = []
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(os.path.join(directory, str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    directory = os.path.abspath(os.getcwd())
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, marker)):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    raise RuntimeError(f"could not locate codebase {number} containing {marker}")


_add_codebase_to_path(os.path.join("authcore", "__init__.py"), 5)

from authcore import b64, decode, encode  # noqa: E402
from authcore.exceptions import InvalidToken  # noqa: E402

SECRET = "s3cret"


def unsigned_token(header, payload):
    """Build a token with an empty signature segment."""
    header_b64 = b64.b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = b64.b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header_b64}.{payload_b64}."


class TestAlgNoneRejected(unittest.TestCase):
    def test_alg_none_rejected(self):
        token = unsigned_token({"alg": "none", "typ": "JWT"}, {"sub": "admin"})
        with self.assertRaises(InvalidToken):
            decode(token, SECRET)

    def test_alg_none_with_attached_signature_rejected(self):
        header_b64 = b64.b64url_encode(b'{"alg":"none","typ":"JWT"}')
        payload_b64 = b64.b64url_encode(b'{"sub":"admin"}')
        fake_sig = b64.b64url_encode(b"not-a-real-signature")
        with self.assertRaises(InvalidToken):
            decode(f"{header_b64}.{payload_b64}.{fake_sig}", SECRET)

    def test_alg_none_is_not_returned_as_a_valid_payload(self):
        token = unsigned_token({"alg": "none"}, {"sub": "admin"})
        try:
            payload = decode(token, SECRET)
        except InvalidToken:
            payload = None
        self.assertNotEqual(payload, {"sub": "admin"})

    def test_alg_none_is_rejected_case_insensitively(self):
        for alg in ("none", "None", "NONE", "nonE"):
            with self.subTest(alg=alg):
                token = unsigned_token({"alg": alg, "typ": "JWT"}, {"sub": "admin"})
                with self.assertRaises(InvalidToken):
                    decode(token, SECRET)

    def test_other_algorithms_are_rejected(self):
        for alg in ("HS512", "RS256", "ES256", "HS256-none"):
            with self.subTest(alg=alg):
                token = unsigned_token({"alg": alg, "typ": "JWT"}, {"sub": "user"})
                with self.assertRaises(InvalidToken):
                    decode(token, SECRET)

    def test_missing_alg_is_rejected(self):
        token = unsigned_token({"typ": "JWT"}, {"sub": "user"})
        with self.assertRaises(InvalidToken):
            decode(token, SECRET)


class TestValidTokensStillWork(unittest.TestCase):
    def test_roundtrip(self):
        token = encode({"sub": "user123"}, secret=SECRET)
        self.assertEqual(decode(token, SECRET), {"sub": "user123"})

    def test_tampered_payload_rejected(self):
        token = encode({"sub": "user123"}, secret=SECRET)
        header, payload, sig = token.split(".")
        forged = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        with self.assertRaises(InvalidToken):
            decode(f"{header}.{forged}.{sig}", SECRET)

    def test_wrong_secret_rejected(self):
        token = encode({"sub": "user123"}, secret=SECRET)
        with self.assertRaises(InvalidToken):
            decode(token, "wrong-secret")

    def test_expired_token_rejected(self):
        token = encode({"sub": "user123"}, secret=SECRET, exp=int(time.time()) - 10)
        with self.assertRaises(InvalidToken):
            decode(token, SECRET)

    def test_valid_token_with_future_expiry(self):
        token = encode({"sub": "user123"}, secret=SECRET, exp=int(time.time()) + 1000)
        self.assertEqual(decode(token, SECRET)["sub"], "user123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
