# Issue — Tokens with `alg: none` are accepted as valid

## Summary

The token verifier accepts JWTs whose header declares the `none` algorithm,
returning their payload without checking any signature. An attacker can forge
a token (e.g. set `sub` to an admin user) simply by signing with nothing.

## Reproduce

```bash
python3 -m unittest discover -s tests -t .
```

The test `test_alg_none_rejected` fails.

## Expected behaviour

Any token whose header algorithm is not `HS256` must be rejected with
`InvalidToken`. In particular, `alg: none` must never be trusted.

## Actual behaviour

An `alg: none` token is decoded and returned as if it were validly signed.

## Where to look

Token verification lives in `authcore/jwt.py`, in the `decode` function.
