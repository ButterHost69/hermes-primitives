"""Longest substring without repeating characters."""


def length_of_longest_substring(s: str) -> int:
    """Return the length of the longest substring of `s` with no repeated characters."""
    last_seen = {}
    start = 0
    max_length = 0
    for i, ch in enumerate(s):
        if ch in last_seen and last_seen[ch] >= start:
            start = i
        last_seen[ch] = i
        max_length = max(max_length, i - start + 1)
    return max_length
