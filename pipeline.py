"""Pipeline: run agent roles over a codebase in sequence.

The :class:`Pipeline` holds the shared configuration for a run — the codebase
to operate on, the model, the reasoning level, and an API key for roles that
talk to a model API directly — and :meth:`Pipeline.run` executes the roles in
order, starting with ``reproduce`` and feeding its response into ``diagnose``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from roles import diagnose as diagnose_role
from roles import reproduce as reproduce_role


class Pipeline:
    """Runs roles over a codebase, beginning with the reproduce role.

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
        ]

    def _role_kwargs(self, *, context: str | None = None) -> dict[str, Any]:
        """Shared keyword arguments for role invocations.

        ``observer`` and ``context`` are only included when set, so callers
        that monkeypatch the role functions with minimal signatures keep
        working.
        """
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "thinking": self.reasoning,
            "cwd": str(self.codebase_path),
        }
        if self.observer is not None:
            kwargs["observer"] = self.observer
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

        ``prompt`` is a bug report: it is passed to ``reproduce`` first, and
        that response is passed to ``diagnose``.  Each result is also recorded
        in :attr:`state`.
        """
        results: dict[str, Any] = {}
        previous: Any = prompt
        for name, run_role in self._roles():
            result = run_role(previous)
            results[name] = result
            self.state[name] = result
            previous = result
        return results
