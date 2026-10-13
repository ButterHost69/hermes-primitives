def backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """Return the wait time in seconds before retry `attempt`.

    `attempt` is 1-based: the first retry is attempt 1, which waits `base`
    seconds, and each subsequent attempt doubles the previous delay, capped
    at `max_delay`.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    delay = base * (2 ** attempt)
    return min(delay, max_delay)
