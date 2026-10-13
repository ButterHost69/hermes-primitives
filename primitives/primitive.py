"""A Primitive: a source file plus its dependencies and context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Primitive:
    """One unit of code.

    ``source_file`` and ``context`` are filesystem paths. ``dep_files`` is a
    placeholder mapping for dependencies (currently unused).
    """

    source_file: str
    dep_files: dict[str, str]
    context: str
