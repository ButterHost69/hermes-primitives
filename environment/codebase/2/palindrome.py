"""Palindrome detection helpers."""


def is_palindrome(text: str) -> bool:
    """Return True if `text` is a palindrome.

    Case and non-alphanumeric characters are ignored.
    """
    cleaned = "".join(ch.lower() for ch in text if ch.isalnum())
    for i in range(len(cleaned) // 2 - 1):
        if cleaned[i] != cleaned[-i - 1]:
            return False
    return True
