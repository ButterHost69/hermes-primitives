"""Synchronous request/response session over the Pi RPC client.

:class:`PiSession` owns its :class:`~agent.client.PiClient` and turns Pi's
asynchronous JSONL event stream into a synchronous request/response API:
send a message, wait for the turn to settle, and receive the assistant's
final text back.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Sequence

from .client import PiClient, PiError

# How often ``run`` re-checks the settled event while it waits for a turn to
# end, so a Pi that dies mid-turn is noticed without polling hot.
_SETTLE_POLL_INTERVAL = 0.5


class PiSession:
    """A conversational session that owns its ``PiClient``.

    ``PiClient`` only acknowledges that a prompt was accepted; the answer
    streams in afterwards as events.  ``PiSession`` creates and starts the
    client, listens for those events, and returns the final assistant text
    from :meth:`run`.
    """

    def __init__(
        self,
        *,
        tools: Sequence[str] | None = None,
        model: str | None = None,
        thinking: str | None = None,
        cwd: str | None = None,
        persistent_session: bool = True,
        session_dir: str | None = None,
        timeout: float = 120.0,
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self._parts: list[str] = []
        self._settled = threading.Event()
        # Every record Pi streams is a sign of life; the timeout is measured
        # from the last one, not from the start of the turn.
        self._last_activity = time.monotonic()
        self.client = PiClient(
            tools=tools,
            model=model,
            thinking=thinking,
            cwd=cwd,
            persistent_session=persistent_session,
            session_dir=session_dir,
        )
        self.client.on_event(self._on_event)
        if observer is not None:
            self.client.on_event(observer)
        self.client.start()

    def close(self) -> None:
        """Shut down the underlying client."""
        self.client.close()

    def __enter__(self) -> "PiSession":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Event collection (runs on the client's reader thread)
    # ------------------------------------------------------------------ #
    def _on_event(self, record: dict[str, Any]) -> None:
        self._last_activity = time.monotonic()
        rtype = record.get("type")

        if rtype == "message_update":
            update = record.get("assistantMessageEvent") or {}
            if update.get("type") == "text_delta":
                self._parts.append(update.get("delta", ""))
            return

        if rtype == "message_end":
            message = record.get("message") or {}
            if message.get("role") == "assistant":
                self._replace_with_final_text(message)
            return

        if rtype == "agent_settled":
            self._settled.set()

    def _replace_with_final_text(self, message: dict[str, Any]) -> None:
        """Use the final assistant message's text, when present, over deltas."""
        content = message.get("content")
        if not isinstance(content, list):
            return
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text_parts.append(str(block.get("text", "")))
        text = "".join(text_parts)
        if text:
            self._parts = [text]

    def _clear(self) -> None:
        self._parts.clear()
        self._settled.clear()
        self._last_activity = time.monotonic()

    # ------------------------------------------------------------------ #
    # Request/response API
    # ------------------------------------------------------------------ #
    def run(
        self,
        message: str,
        *,
        steer: bool = False,
        follow_up: bool = False,
        timeout: float | None = None,
    ) -> str:
        """Send ``message`` and return the assistant's final text.

        Blocks until the turn settles or no event arrives for ``timeout``
        seconds.  Raises :class:`~agent.client.PiError` if the prompt is
        rejected, the Pi process dies before the turn settles, or the stream
        goes quiet for too long.
        """
        self._clear()
        response = self.client.prompt(message, steer=steer, follow_up=follow_up)
        if not response.get("success", False):
            raise PiError(response.get("error") or "prompt rejected")
        # Prompt acceptance is not assistant activity; start the idle clock
        # once the turn is actually running.
        self._last_activity = time.monotonic()
        self._wait_for_settle(timeout if timeout is not None else self.timeout)
        return "".join(self._parts)

    def _wait_for_settle(self, timeout: float) -> None:
        """Block until the turn settles, fails, or goes quiet for ``timeout``.

        ``timeout`` is an inactivity limit, not a total wall-clock limit:
        every record Pi streams resets the clock, so an actively working turn
        is never cut off no matter how long it takes.  Waiting on the settled
        event alone holds the caller for the whole timeout when Pi dies
        mid-turn: a process that is gone can never settle.  Polling the
        client's liveness beside the event turns that into an immediate
        failure, while a turn that settles on time returns at once -- the
        event wakes its own wait, so the poll adds no latency to a healthy
        turn.
        """
        while True:
            idle = time.monotonic() - self._last_activity
            if idle >= timeout:
                raise PiError(
                    "timed out waiting for the assistant to finish: "
                    f"no activity for {timeout:g}s"
                )
            if self._settled.wait(min(timeout - idle, _SETTLE_POLL_INTERVAL)):
                return
            if not self.client.running:
                raise PiError("pi exited before the turn settled")

    def retry(
        self,
        *,
        error: str | None = None,
        message: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Ask the model to fix a previous response.

        Sends a corrective message in the *same* session (no new session is
        started) and returns the new text.  When ``message`` is given it is
        sent verbatim; otherwise the default JSON-retry message is used.
        """
        if message is None:
            message = (
                "Your previous response was not valid JSON. "
                "Respond again with only valid JSON and no other text."
            )
            if error:
                message += f"\n\nThe parser reported: {error}"
        return self.run(message, timeout=timeout)
