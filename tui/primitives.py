"""Live per-primitive streams for the fanned-out primitive_manager role.

``PrimitiveManager.call_all`` runs every registered primitive at once, so the
records a fan-out streams arrive interleaved across agents.  The pipeline tags
each of those records with the source file the primitive that emitted it owns
(:data:`PRIMITIVE_KEY`); :class:`PrimitiveFeed` keeps one bounded
:class:`~tui.transcript.Transcript` per primitive, records which state each one
is in — running, done, or failed — and renders the fan-out either as every
stream grouped under a labelled header, or as the single stream the operator
chose to look at.
"""

from __future__ import annotations

import time

from .screen import wrap_text
from .transcript import KIND_ROLE, KIND_SPACER, Transcript

# Record key the pipeline stamps on every record a primitive streams, so the
# primitives of a fan-out can be told apart one by one.
PRIMITIVE_KEY = "primitive"

# ``files`` on a fan-out record, ``report`` on a report, ``error`` on a failure.
FILES_KEY = "files"
REPORT_KEY = "report"
ERROR_KEY = "error"

# Record types the pipeline publishes about a fan-out as it runs.
FANNED_OUT_TYPE = "primitives_fanned_out"
REPORTED_TYPE = "primitive_reported"
FAILED_TYPE = "primitive_failed"

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

# The marker beside a primitive's file name, and the palette token the name is
# painted with while it is in that state.
_MARKERS = {
    STATUS_RUNNING: "\u27f3",
    STATUS_DONE: "\u2713",
    STATUS_ERROR: "\u2717",
}
_STATUS_COLORS = {
    STATUS_RUNNING: "accent",
    STATUS_DONE: "success",
    STATUS_ERROR: "error",
}


def abbreviate(path: str, width: int) -> str:
    """Shorten ``path`` to ``width`` columns, keeping its head and its tail.

    A path that does not fit keeps its leading directories and its file name
    and drops the middle, so ``environment/codebase/src/module.py`` reads as
    ``environme\u2026/module.py``.  A path with no directory to drop is cut at
    the end.
    """
    if width <= 0:
        return ""
    if len(path) <= width:
        return path
    head, separator, tail = path.rpartition("/")
    if separator and len(tail) + 1 < width - 1:
        kept = head[: max(0, width - len(tail) - 2)]
        return f"{kept}\u2026/{tail}"
    return path[: width - 1] + "\u2026"


class PrimitiveFeed:
    """The primitives a fan-out is running, each with its own live stream.

    ``order`` lists the source files in registration order — the order the
    diagnosis named them in — and ``streams`` holds the transcript of each
    one.  A primitive the fan-out never mentioned is registered the first time
    its records arrive, so nothing it streamed is dropped.
    """

    def __init__(self, max_lines: int = 2000) -> None:
        self.max_lines = max_lines
        self.order: list[str] = []
        self.streams: dict[str, Transcript] = {}
        self.status: dict[str, str] = {}
        self.started_at: dict[str, float] = {}
        self.ended_at: dict[str, float | None] = {}

    # ------------------------------------------------------------------ #
    # Registry
    # ------------------------------------------------------------------ #
    def register(self, source_file: str) -> None:
        """Add ``source_file`` to the fan-out if it is not there yet."""
        if source_file in self.streams:
            return
        self.order.append(source_file)
        self.streams[source_file] = Transcript(max_lines=self.max_lines)
        self.status[source_file] = STATUS_RUNNING
        self.started_at[source_file] = time.monotonic()
        self.ended_at[source_file] = None

    def transcript_for(self, source_file: str) -> Transcript:
        """Return the stream of ``source_file``, registering it if needed."""
        self.register(source_file)
        return self.streams[source_file]

    def __bool__(self) -> bool:
        return bool(self.order)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    def note_report(self, source_file: str, report: str) -> None:
        """Record the report ``source_file`` came back with; the run is over."""
        self.register(source_file)
        if report:
            self.streams[source_file].result(report)
        self._settle(source_file, STATUS_DONE)

    def note_failure(self, source_file: str, error: str) -> None:
        """Record that ``source_file``'s agent raised ``error``.

        The fan-out reports a failure twice — once as it happens, and again
        when it raises — so a primitive already marked failed keeps the reason
        it was given first.
        """
        self.register(source_file)
        if self.status[source_file] == STATUS_ERROR:
            return
        self.streams[source_file].error(f"\u2717 {error}")
        self._settle(source_file, STATUS_ERROR)

    def finish(self) -> None:
        """Settle every primitive still marked running.

        The fan-out is over, so a primitive the pipeline never reported on
        still counts as done rather than spinning forever.
        """
        for source_file in self.order:
            if self.status[source_file] == STATUS_RUNNING:
                self._settle(source_file, STATUS_DONE)

    def _settle(self, source_file: str, status: str) -> None:
        self.status[source_file] = status
        self.ended_at[source_file] = time.monotonic()

    def marker(self, source_file: str) -> str:
        """Return the glyph ``source_file``'s state is shown with."""
        return _MARKERS.get(self.status.get(source_file), " ")

    def status_color(self, source_file: str) -> str:
        """Return the palette token ``source_file``'s name is painted with."""
        return _STATUS_COLORS.get(self.status.get(source_file), "muted")

    def detail(self, source_file: str) -> str:
        """Return the trailing note beside ``source_file``'s header.

        A running primitive shows how long it has been going; one that
        finished shows how long it took.
        """
        started = self.started_at.get(source_file)
        if started is None:
            return ""
        if self.status.get(source_file) == STATUS_ERROR:
            return "failed"
        ended = self.ended_at.get(source_file)
        end = time.monotonic() if ended is None else ended
        return f"{end - started:.1f}s"

    def summary(self) -> str:
        """Return the fan-out as ``"3 primitives · 1 running …"``."""
        counts: dict[str, int] = {}
        for source_file in self.order:
            status = self.status[source_file]
            counts[status] = counts.get(status, 0) + 1
        parts = [f"{len(self.order)} primitives"]
        for status in (STATUS_RUNNING, STATUS_DONE, STATUS_ERROR):
            if counts.get(status):
                parts.append(f"{counts[status]} {status}")
        return " \u00b7 ".join(parts)

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def rows(
        self, width: int, focus: str | None = None
    ) -> list[tuple[str, str, str]]:
        """Build the ``(kind, label, text)`` rows of the fan-out.

        With ``focus`` given, only that primitive's stream is built, under one
        header naming its file.  Otherwise every primitive is built in
        registration order, each under its own header, so the whole fan-out
        reads as a column of labelled streams.  ``label`` is the source file
        the row came from — empty on headers and spacers — and the caller
        paints it beside ``text``.
        """
        if focus is not None and focus in self.streams:
            return [self._header(focus), *self._stream(focus, width)]
        rows: list[tuple[str, str, str]] = []
        for source_file in self.order:
            if rows:
                rows.append((KIND_SPACER, "", ""))
            rows.append(self._header(source_file))
            rows.extend(self._stream(source_file, width))
        return rows

    def _header(self, source_file: str) -> tuple[str, str, str]:
        """Build the banner row that names one primitive and its state."""
        return (
            KIND_ROLE,
            source_file,
            f"{self.marker(source_file)}  {self.detail(source_file)}",
        )

    def _stream(
        self, source_file: str, width: int
    ) -> list[tuple[str, str, str]]:
        """Build the wrapped stream rows of one primitive, each labelled.

        The line the primitive is still writing is built with the finished
        ones: that partial row is what a live view is for.
        """
        label = source_file if len(self.order) > 1 else ""
        stream = self.streams[source_file]
        rows: list[tuple[str, str, str]] = []
        lines: list[tuple[str, str]] = list(stream.lines)
        partial = stream.partial()
        if partial[1]:
            lines.append(partial)
        for kind, text in lines:
            if kind == KIND_SPACER:
                rows.append((KIND_SPACER, "", ""))
                continue
            for chunk in wrap_text(text, max(1, width)):
                rows.append((kind, label, chunk))
        return rows
