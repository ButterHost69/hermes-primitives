"""Visitor tracking helpers."""


def record_visit(visitor_id: int, seen: list = []) -> list:
    """Record a visitor id and return the ids recorded by this call.

    Each call should return a fresh list containing only the visitors
    recorded during that call.
    """
    seen.append(visitor_id)
    return seen
