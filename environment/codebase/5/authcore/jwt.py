import hashlib
import hmac
import json
import time

from .b64 import b64url_decode, b64url_encode
from .exceptions import InvalidToken


def _sign(secret: str, data: bytes) -> bytes:
    return hmac.new(secret.encode(), data, hashlib.sha256).digest()


def encode(payload: dict, secret: str, exp: int | None = None) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    if exp is not None:
        body["exp"] = exp
    header_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = b64url_encode(_sign(secret, signing_input))
    return f"{header_b64}.{payload_b64}.{signature}"


def decode(token: str, secret: str) -> dict:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise InvalidToken("malformed token")

    try:
        header = json.loads(b64url_decode(header_b64))
    except Exception:
        raise InvalidToken("invalid header")

    if header.get("alg") == "none":
        return json.loads(b64url_decode(payload_b64))

    if header.get("alg") != "HS256":
        raise InvalidToken("unsupported algorithm")

    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = b64url_encode(_sign(secret, signing_input))
    if not hmac.compare_digest(expected, signature_b64):
        raise InvalidToken("signature mismatch")

    try:
        payload = json.loads(b64url_decode(payload_b64))
    except Exception:
        raise InvalidToken("invalid payload")

    if payload.get("exp") is not None and payload["exp"] < time.time():
        raise InvalidToken("token expired")

    return payload
