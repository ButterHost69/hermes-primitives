"""Pipeline: run agent roles over a codebase in sequence.

The :class:`Pipeline` holds the shared configuration for a run — the codebase
to operate on, the model, the reasoning level, and an API key for roles that
talk to a model API directly — and :meth:`Pipeline.run` executes the roles in
order: ``reproduce`` answers whether the bug is real, ``diagnose`` names the
files responsible for it, and ``primitive_manager`` registers a Primitive per
diagnosed file and calls each one to apply the fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from roles import diagnose as diagnose_role
from roles import reproduce as reproduce_role
from roles.primitive_manager import PrimitiveManager


def _diagnosis_findings(diagnosis: Any) -> list[dict[str, Any]]:
    """Return the ``findings`` of a diagnosis, ignoring everything else."""
    if not isinstance(diagnosis, dict):
        return []
    findings = diagnosis.get("findings")
    if not isinstance(findings, list):
        return []
    return [finding for finding in findings if isinstance(finding, dict)]


def _finding_objective(finding: dict[str, Any]) -> str:
    """Render one finding as the objective its primitive is called with."""
    parts: list[str] = []
    explanation = finding.get("explanation")
    if isinstance(explanation, str) and explanation.strip():
        parts.append(f"Why this file is implicated:\n{explanation.strip()}")
    fixes = finding.get("fixes")
    if isinstance(fixes, list):
        lines = [f"- {fix}" for fix in fixes if isinstance(fix, str) and fix.strip()]
        if lines:
            parts.append("Suggested fixes:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _primitive_work(diagnosis: Any) -> list[tuple[str, str]]:
    """Pair every diagnosed file with the objective for its primitive.

    A finding without a ``file`` path names no artifact, and one explaining
    nothing and proposing no fix carries no work, so both are skipped.
    """
    work: list[tuple[str, str]] = []
    for finding in _diagnosis_findings(diagnosis):
        source_file = finding.get("file")
        if not isinstance(source_file, str) or not source_file:
            continue
        objective = _finding_objective(finding)
        if not objective:
            continue
        work.append((source_file, objective))
    return work


class Pipeline:
    """Runs roles over a codebase, beginning with the reproduce role.

    The chain is ``reproduce`` → ``diagnose`` → ``primitive_manager``: each
    role receives the previous one's result, and the last one acts on it.

    Parameters
    ----------
    codebase_path:
        Path to the codebase the roles will work on.
    model_name:
        Model to use (exact id or fuzzy match, as accepted by Pi).
    reasoning:
        Reasoning level (``off``, ``low``, ``medium``, ``high``, …), mapped to
        Pi's ``thinking`` level.
    api_key:
        API key for roles that call a model API directly. The roles run
        through Pi (which supplies its own credentials), so this is held for
        later use rather than forwarded.
    """

    def __init__(
        self,
        codebase_path: str | Path,
        model_name: str,
        reasoning: str,
        api_key: str,
        *,
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.codebase_path = Path(codebase_path).resolve()
        self.model_name = model_name
        self.reasoning = reasoning
        self.api_key = api_key
        self.observer = observer
        self.state: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Role sequence
    # ------------------------------------------------------------------ #
    def _roles(self) -> list[tuple[str, Callable[[Any], Any]]]:
        """Return the ordered role sequence as ``(name, callable)`` pairs.

        Each callable receives the previous role's result; the first receives
        the user prompt.
        """
        return [
            ("reproduce", self._run_reproduce),
            ("diagnose", self._run_diagnose),
            ("primitive_manager", self._run_primitive_manager),
        ]

    def _session_kwargs(self) -> dict[str, Any]:
        """Model, reasoning, and working directory for a role's session.

        ``observer`` is only included when one is installed, so callers that
        monkeypatch the role functions with minimal signatures keep working.
        """
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "thinking": self.reasoning,
            "cwd": str(self.codebase_path),
        }
        if self.observer is not None:
            kwargs["observer"] = self.observer
        return kwargs

    def _role_kwargs(self, *, context: str | None = None) -> dict[str, Any]:
        """Shared keyword arguments for role invocations.

        ``context`` is only included when given, for the same reason.
        """
        kwargs = self._session_kwargs()
        if context is not None:
            kwargs["context"] = context
        return kwargs

    def _run_reproduce(
        self, prompt: str, *, context: str | None = None
    ) -> dict[str, Any]:
        """First role: reproduce the bug described by ``prompt``."""
        return reproduce_role.reproduce(prompt, **self._role_kwargs(context=context))

    def _run_diagnose(
        self, reproduction: dict[str, Any], *, context: str | None = None
    ) -> dict[str, Any]:
        """Second role: diagnose the response produced by ``reproduce``."""
        return diagnose_role.diagnose(
            json.dumps(reproduction), **self._role_kwargs(context=context)
        )

    def _run_primitive_manager(
        self, diagnosis: dict[str, Any], *, context: str | None = None
    ) -> dict[str, str]:
        """Third role: apply the changes ``diagnose`` called for.

        The diagnosis is the registry's input: it names the files to register
        and supplies the objective each primitive is called with.  The
        primitives run under this pipeline's model, reasoning level, working
        directory, and observer, and their reports come back keyed by source
        file.  The registry is closed afterwards, so no Pi session outlives
        the role.
        """
        work = _primitive_work(diagnosis)
        objectives = dict(work)
        if context:
            objectives = {
                source_file: f"{objective}\n\n{context}"
                for source_file, objective in objectives.items()
            }
        manager = PrimitiveManager(objectives=objectives)
        manager.create_all(
            [source_file for source_file, _ in work], **self._session_kwargs()
        )
        try:
            return manager.call_all()
        finally:
            manager.close()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def role_names(self) -> list[str]:
        """Return the ordered role names, in execution order."""
        return [name for name, _ in self._roles()]

    def run_step(
        self, name: str, previous: Any, *, context: str | None = None
    ) -> Any:
        """Run the single role named ``name`` with ``previous`` as its input.

        ``previous`` is the same value that role would receive from the chain
        inside :meth:`run` — the user prompt for the first role, and the
        previous role's result for every later role.  ``context``, when given,
        is passed to the role as extra operator guidance.  The result is
        recorded in :attr:`state`.

        Raises :class:`KeyError` when ``name`` is not a known role.
        """
        for role_name, run_role in self._roles():
            if role_name == name:
                result = run_role(previous, context=context)
                self.state[name] = result
                return result
        raise KeyError(f"unknown role: {name}")

    def run(self, prompt: str) -> dict[str, Any]:
        """Run the roles in order and return their results keyed by name.

        ``prompt`` is a bug report: it is passed to ``reproduce`` first, that
        response is passed to ``diagnose``, and the diagnosis is handed to
        ``primitive_manager``.  Each result is also recorded in :attr:`state`.
        """
        results: dict[str, Any] = {}
        previous: Any = prompt
        for name, run_role in self._roles():
            result = run_role(previous)
            results[name] = result
            self.state[name] = result
            previous = result
        return results
