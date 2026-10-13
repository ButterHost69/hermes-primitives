"""Helpers for validating model output as JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonCheck:
    """Result of :func:`verify_json`.

    ``ok`` is ``True`` when the text parses as JSON.  On success, ``value``
    holds the decoded object; on failure, ``error`` holds the parser's
    message (or ``None`` on success).
    """

    ok: bool
    value: Any = None
    error: str | None = None


def verify_json(text: str) -> JsonCheck:
    """Check whether ``text`` is valid JSON and return the parsed value.

    Leading/trailing whitespace is ignored.  A JSON ``null`` parses
    successfully with ``value`` set to ``None``; ``ok`` distinguishes that
    from a parse failure.
    """
    try:
        return JsonCheck(ok=True, value=json.loads(text))
    except (ValueError, TypeError) as exc:
        return JsonCheck(ok=False, error=str(exc))
