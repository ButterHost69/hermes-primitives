"""PrimitiveManager: create, remove, look up, and run Primitive objects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from agent import PiError
from agent.gated_client import Policy
from roles.primitive import Primitive

# A primitive registered without a stated objective still has to be told what
# to address.
UNSTATED_OBJECTIVE = (
    "Determine what this file needs and change only that. Report the change "
    "you applied, or that no local change was necessary."
)


class PrimitiveRunError(PiError):
    """Raised when at least one primitive failed during a fanned-out ``call_all``.

    A failing primitive never takes its siblings' reports down with it:
    ``reports`` carries every report that came back, keyed by source file, and
    ``failures`` maps each path whose primitive raised to the exception it
    raised.  Both follow the order the primitives were registered in.
    """

    def __init__(
        self, failures: dict[str, BaseException], reports: dict[str, str]
    ) -> None:
        paths = ", ".join(failures)
        super().__init__(f"{len(failures)} primitive(s) failed: {paths}")
        self.failures = failures
        self.reports = reports


class PrimitiveCloseError(PiError):
    """Raised when at least one Primitive session refused to close.

    Every primitive is still closed -- the walk only raises once the whole
    registry has been attended to -- and ``failures`` maps each path whose
    session raised to the exception it raised, in registry order.
    """

    def __init__(self, failures: dict[str, BaseException]) -> None:
        paths = ", ".join(failures)
        super().__init__(f"{len(failures)} primitive session(s) failed to close: {paths}")
        self.failures = failures


class PrimitiveManager:
    """Owns the registry of Primitives, keyed by ``source_file`` path.

    ``objectives`` maps a source file to the work its primitive is called
    with; a file without an entry is given :data:`UNSTATED_OBJECTIVE`, so a
    primitive registered outside a diagnosis still knows what to address.
    :meth:`call_all` fans the registry out over a pool and owns closing it,
    so no Pi session outlives a fan-out even when a primitive fails.
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
        read_policy: Policy | None = None,
    ) -> Primitive:
        """Create a Primitive, register it under ``source_file``, return it.

        The model, reasoning level, working directory, and observer configure
        the gated session the primitive opens.  ``read_policy``, when given,
        confines every non-write tool (the per-artifact write gate is always
        applied).  A Primitive already registered under ``source_file`` is
        returned as it is: registering a fresh one would drop the Pi session it
        has attached and the dependency relationships it has accumulated.
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
            read_policy=read_policy,
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
        read_policy: Policy | None = None,
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
                read_policy=read_policy,
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

    def call_all(
        self,
        *,
        max_workers: int | None = None,
        on_report: Callable[[str, str], None] | None = None,
        on_failure: Callable[[str, BaseException], None] | None = None,
    ) -> dict[str, str]:
        """Run every registered Primitive at once and collect their reports.

        The registry is fanned out over the workers of a pool built for this
        call: one worker per primitive, or ``max_workers`` of them when that is
        given, so a large diagnosis can be capped.  ``1`` runs the registry in
        registration order, the way a single-threaded caller expects.

        Each report is collected the moment it comes back and handed to
        ``on_report`` — from the calling thread, in completion order — and a
        primitive that raised is handed to ``on_failure`` the same way, so a
        caller can follow the fan-out as it happens instead of only reading
        its outcome.  The returned mapping itself is keyed by source file in
        registration order, so the result reads the same whichever way the
        primitives finished.  A path whose primitive produces no report is
        left out.

        Every session is closed before this returns, whichever way it ends:
        the workers are joined first, so no agent is torn down while it is
        still running, and closing is idempotent, so a caller that closes
        again closes nothing twice.  A primitive that raises does not stop the
        run — its exception is kept and its siblings still run — but the call
        then fails with :class:`PrimitiveRunError`, carrying the reports that
        did come back beside the failures that did not.
        """
        registry = list(self.primitive_map.items())
        if max_workers is not None and max_workers < 1:
            raise ValueError(f"max_workers must be at least 1: {max_workers}")
        if not registry:
            return {}
        workers = (
            len(registry)
            if max_workers is None
            else min(max_workers, len(registry))
        )
        executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="hms-primitive"
        )
        try:
            try:
                futures = {
                    executor.submit(self.call_primitive, source_file): source_file
                    for source_file, _ in registry
                }
                reports: dict[str, str] = {}
                failures: dict[str, BaseException] = {}
                for future in as_completed(futures):
                    source_file = futures[future]
                    # One primitive failing must not abandon the run or the
                    # sessions its siblings still hold.
                    try:
                        report = future.result()
                    except BaseException as exc:
                        failures[source_file] = exc
                        if on_failure is not None:
                            on_failure(source_file, exc)
                        continue
                    if report is None:
                        continue
                    reports[source_file] = report
                    if on_report is not None:
                        on_report(source_file, report)
            finally:
                # The workers are joined before anything is closed, so no
                # session is torn down while its agent is still inside a run.
                executor.shutdown(wait=True)
        except BaseException:
            # The fan-out itself broke -- an interrupted collection, a
            # submission the operating system refused.  The registry is
            # still closed before the failure travels on.
            self.close()
            raise
        ordered_reports = {
            source_file: reports[source_file]
            for source_file, _ in registry
            if source_file in reports
        }
        failure = None
        if failures:
            failure = PrimitiveRunError(
                {
                    source_file: failures[source_file]
                    for source_file, _ in registry
                    if source_file in failures
                },
                ordered_reports,
            )
        try:
            # Closing is the last thing a successful fan-out does, so a
            # session that refuses to close cannot hide the outcome of the
            # run it went out with.
            self.close()
        finally:
            if failure is not None:
                raise failure
        return ordered_reports

    def close(self) -> None:
        """Close the attached Pi session of every registered primitive.

        One session refusing to close does not strand the ones registered
        behind it: every primitive is closed, and the paths that could not be
        are reported afterwards with :class:`PrimitiveCloseError`.
        """
        failures: dict[str, BaseException] = {}
        for source_file, primitive in self.primitive_map.items():
            try:
                primitive.close()
            except BaseException as exc:
                failures[source_file] = exc
        if failures:
            raise PrimitiveCloseError(failures)

    def _objective(self, source_file: str) -> str:
        """Return the objective the Primitive under ``source_file`` is called with."""
        return self.objectives.get(source_file, UNSTATED_OBJECTIVE)
