"""PrimitiveManager: create, remove, and look up Primitive objects."""

from __future__ import annotations

from roles.primitive import Primitive


class PrimitiveManager:
    """Owns the registry of Primitives, keyed by ``source_file`` path."""

    def __init__(self) -> None:
        self.primitive_map: dict[str, Primitive] = {}

    def create(
        self,
        source_file: str,
        dep_files: dict[str, str] | None = None,
    ) -> Primitive:
        """Create a Primitive, register it under ``source_file``, return it."""
        primitive = Primitive(
            source_file=source_file,
            dep_files=dep_files if dep_files is not None else {},
        )
        self.primitive_map[source_file] = primitive
        return primitive

    def remove(self, source_file: str) -> bool:
        """Remove the Primitive registered under ``source_file``.

        Returns ``True`` if one was removed, ``False`` otherwise.
        """
        return self.primitive_map.pop(source_file, None) is not None

    def search(self, source_file: str) -> Primitive | None:
        """Return the Primitive registered under ``source_file``, or ``None``."""
        return self.primitive_map.get(source_file)

    def close(self) -> None:
        """Close the attached Pi session of every registered primitive."""
        for primitive in self.primitive_map.values():
            primitive.close()
