# AuthCore

AuthCore is a small, dependency-free JWT authentication service written in
Python using only the standard library. It creates and verifies JSON Web Tokens
(JWTs) signed with **HMAC-SHA256** — the `HS256` algorithm.

## Features

- Create signed JWTs with an optional expiration time (`exp`).
- Verify JWTs: checks structure, algorithm, signature, and expiry.
- Tamper-proof: any modification to the header or payload invalidates the token.
- Pure standard library — no external dependencies.

## The HS256 signing scheme

A JWT consists of three base64url-encoded segments separated by dots:

```
<header>.<payload>.<signature>
```

1. **Header** — JSON describing the token, e.g. `{"alg":"HS256","typ":"JWT"}`.
2. **Payload** — JSON containing the claims (e.g. `sub`, `exp`).
3. **Signature** — the security-critical part.

The signature is computed as:

```
signature = HMAC-SHA256(secret, "<header>.<payload>")
```

where the secret is a shared symmetric key known only to the issuer and the
verifier. Because HMAC-SHA256 is a keyed hash, only parties holding the secret
can produce a valid signature. On verification, AuthCore recomputes the
signature over the received header and payload and compares it to the provided
signature using `hmac.compare_digest` (constant-time comparison). If anything
was altered, the recomputed signature will differ and the token is rejected.

`HS256` means "HMAC using SHA-256", which provides 256 bits of security.

## Usage

```python
from authcore import encode, decode, InvalidToken

# Create a token
token = encode({"sub": "user123"}, secret="s3cret")

# Create a token that expires at a specific Unix timestamp
token = encode({"sub": "user123"}, secret="s3cret", exp=1710000000)

# Verify a token
try:
    payload = decode(token, "s3cret")
    print(payload)  # {"sub": "user123"}
except InvalidToken as e:
    print("Invalid token:", e)
```

## Running the tests

From the project root, run either:

```bash
python3 -m unittest discover -s tests -t . -v
```

or, if you have pytest installed:

```bash
python3 -m pytest
```

All tests should pass.
