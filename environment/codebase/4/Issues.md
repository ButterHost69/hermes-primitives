# Issue — Exponential backoff is one step too aggressive

## Summary

The exponential backoff helper returns delays that are one doubling step higher
than intended. The first retry already waits twice as long as it should, which
adds unnecessary latency and load under retry storms.

## Reproduce

```bash
python3 -m unittest discover -s tests -t .
```

The tests `test_first_retry_waits_base` and `test_exponential_growth` fail.

## Expected behaviour

Attempt 1 waits `base` seconds, attempt 2 waits `2×base`, attempt 3 waits
`4×base`, and so on, capped at `max_delay`.

## Actual behaviour

Attempt 1 waits `2×base`, attempt 2 waits `4×base`, attempt 3 waits `8×base`.

## Where to look

The backoff calculation lives in `queuemaster/backoff.py`.
