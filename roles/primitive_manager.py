"""PrimitiveManager: create, remove, look up, and run Primitive objects."""

from __future__ import annotations

from typing import Any, Callable

from roles.primitive import Primitive

# A primitive registered without a stated objective still has to be told what
# to address.
UNSTATED_OBJECTIVE = (
    "Determine what this file needs and change only that. Report the change "
    "you applied, or that no local change was necessary."
)


class PrimitiveManager:
    """Owns the registry of Primitives, keyed by ``source_file`` path.

    ``objectives`` maps a source file to the work its primitive is called
    with; a file without an entry is given :data:`UNSTATED_OBJECTIVE`, so a
    primitive registered outside a diagnosis still knows what to address.
    """

    def __init__(self, objectives: dict[str, str] | None = None) -> None:
        self.primitive_map: dict[str, Primitive] = {}
        self.objectives: dict[str, str] = dict(objectives or {})

    # ------------------------------------------------------------------ #
    # Registry
    # ------------------------------------------------------------------ #
    def create_primitive(
        self,
        source_file: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        cwd: str | None = None,
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> Primitive:
        """Create a Primitive, register it under ``source_file``, return it.

        The model, reasoning level, working directory, and observer configure
        the gated session the primitive opens.  A Primitive already registered
        under ``source_file`` is returned as it is: registering a fresh one
        would drop the Pi session it has attached and the dependency
        relationships it has accumulated.
        """
        registered = self.primitive_map.get(source_file)
        if registered is not None:
            return registered
        primitive = Primitive(
            source_file=source_file,
            model=model,
            thinking=thinking,
            cwd=cwd,
            observer=observer,
        )
        self.primitive_map[source_file] = primitive
        return primitive

    def create_all(
        self,
        source_files: list[str],
        *,
        model: str | None = None,
        thinking: str | None = None,
        cwd: str | None = None,
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[Primitive]:
        """Register a Primitive for every path in ``source_files``.

        Returns the primitives in the order the paths were given; a repeated
        path resolves to the primitive already registered under it.
        """
        return [
            self.create_primitive(
                source_file,
                model=model,
                thinking=thinking,
                cwd=cwd,
                observer=observer,
            )
            for source_file in source_files
        ]

    def remove(self, source_file: str) -> bool:
        """Remove the Primitive registered under ``source_file``.

        Returns ``True`` if one was removed, ``False`` otherwise.
        """
        return self.primitive_map.pop(source_file, None) is not None

    def search(self, source_file: str) -> Primitive | None:
        """Return the Primitive registered under ``source_file``, or ``None``."""
        return self.primitive_map.get(source_file)

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def call_primitive(self, source_file: str) -> str | None:
        """Run the Primitive registered under ``source_file``.

        The primitive is called with the objective the manager holds for its
        path, and its report comes back.  A path with no primitive registered
        is not an error -- there is nothing to call, so ``None`` comes back and
        the registry is left as it was.
        """
        primitive = self.primitive_map.get(source_file)
        if primitive is None:
            return None
        return primitive.run(self._objective(source_file))

    def call_all(self) -> dict[str, str]:
        """Run every registered Primitive and return their reports by path.

        This is :meth:`call_primitive` applied to the whole registry: a
        primitive that produces no report is left out of the result.
        """
        reports: dict[str, str] = {}
        for source_file in self.primitive_map:
            report = self.call_primitive(source_file)
            if report is None:
                continue
            reports[source_file] = report
        return reports

    def close(self) -> None:
        """Close the attached Pi session of every registered primitive."""
        for primitive in self.primitive_map.values():
            primitive.close()

    def _objective(self, source_file: str) -> str:
        """Return the objective the Primitive under ``source_file`` is called with."""
        return self.objectives.get(source_file, UNSTATED_OBJECTIVE)
