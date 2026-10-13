import time
import unittest

from authcore import b64
from authcore import decode, encode
from authcore.exceptions import InvalidToken


class TestJwt(unittest.TestCase):
    def test_roundtrip(self):
        token = encode({"sub": "user123"}, secret="s3cret")
        self.assertEqual(decode(token, "s3cret"), {"sub": "user123"})

    def test_tampered_payload_rejected(self):
        token = encode({"sub": "user123"}, secret="s3cret")
        header, payload, sig = token.split(".")
        forged = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        with self.assertRaises(InvalidToken):
            decode(f"{header}.{forged}.{sig}", "s3cret")

    def test_alg_none_rejected(self):
        header = b64.b64url_encode(b'{"alg":"none","typ":"JWT"}')
        payload = b64.b64url_encode(b'{"sub":"admin"}')
        token = f"{header}.{payload}."
        with self.assertRaises(InvalidToken):
            decode(token, "s3cret")

    def test_expired_rejected(self):
        token = encode({"sub": "user123"}, secret="s3cret", exp=int(time.time()) - 10)
        with self.assertRaises(InvalidToken):
            decode(token, "s3cret")


if __name__ == "__main__":
    unittest.main()
