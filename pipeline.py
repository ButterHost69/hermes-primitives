"""Pipeline: run agent roles over a codebase in sequence.

The :class:`Pipeline` holds the shared configuration for a run — the codebase
to operate on, the model, the reasoning level, and an API key for roles that
talk to a model API directly — and :meth:`Pipeline.run` executes the roles in
order, starting with ``triage``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from roles import triage as triage_role


class Pipeline:
    """Runs roles over a codebase, beginning with the triage role.

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
        API key for roles that call a model API directly. Triage runs through
        Pi (which supplies its own credentials), so this is held for later
        roles rather than forwarded to triage.
    """

    def __init__(
        self,
        codebase_path: str | Path,
        model_name: str,
        reasoning: str,
        api_key: str,
    ) -> None:
        self.codebase_path = Path(codebase_path).resolve()
        self.model_name = model_name
        self.reasoning = reasoning
        self.api_key = api_key
        self.state: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Role sequence
    # ------------------------------------------------------------------ #
    def _roles(self) -> list[tuple[str, Callable[[], Any]]]:
        """Return the ordered role sequence as ``(name, callable)`` pairs."""
        return [
            ("triage", self._run_triage),
        ]

    def _run_triage(self) -> dict[str, Any]:
        """Run the first role: triage the codebase."""
        return triage_role.triage(
            f"Triage the codebase at {self.codebase_path}.",
            model=self.model_name,
            thinking=self.reasoning,
            cwd=str(self.codebase_path),
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def run(self) -> dict[str, Any]:
        """Run the roles in order and return their results keyed by name.

        Each role's result is also recorded in :attr:`state`.
        """
        results: dict[str, Any] = {}
        for name, run_role in self._roles():
            result = run_role()
            results[name] = result
            self.state[name] = result
        return results
