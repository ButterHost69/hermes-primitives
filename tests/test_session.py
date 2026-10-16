"""Tests for the PiSession abstraction layer."""

from __future__ import annotations

import time

import pytest

import agent.session as session_module
from agent.client import PiError
from agent.session import PiSession


class StubClient:
    """A controllable stand-in for the ``PiClient`` a ``PiSession`` owns."""

    def __init__(self, *, events=None, response=None):
        self.config: dict = {}
        self.started = False
        self.closed = False
        self.handlers = []
        self.prompt_calls: list[tuple] = []
        self.response = response if response is not None else {
            "success": True, "data": {"disposition": "started"},
        }
        self.events = events if events is not None else []
        self.running = True
        self.die_after_prompt = False

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def on_event(self, handler):
        self.handlers.append(handler)

    def prompt(self, message, steer=False, follow_up=False):
        self.prompt_calls.append((message, steer, follow_up))
        for record in self.events:
            for handler in list(self.handlers):
                handler(record)
        if self.die_after_prompt:
            self.running = False
        return self.response

    def emit(self, record):
        for handler in list(self.handlers):
            handler(record)


def settled_events(text):
    """Build the event stream for a turn that ends with ``text``."""
    events = []
    for ch in text:
        events.append({
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta", "delta": ch},
        })
    events.append({"type": "message_end", "message": {"role": "assistant"}})
    events.append({"type": "agent_settled", "aborted": False})
    return events


@pytest.fixture
def make_session(monkeypatch):
    """Patch ``PiClient`` so PiSession builds a StubClient.

    Returns a helper that constructs ``(client, session)`` pairs, forwarding
    any extra keyword arguments to ``PiSession`` as client configuration.
    """

    def _make(events=None, response=None, timeout=5.0, **config):
        client = StubClient(events=events, response=response)

        def make_client(**kwargs):
            client.config = kwargs
            return client

        monkeypatch.setattr(session_module, "PiClient", make_client)
        session = PiSession(timeout=timeout, **config)
        return client, session

    return _make


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #
def test_init_registers_event_handler_and_timeout(make_session):
    client, session = make_session(timeout=9.0)
    assert session.client is client
    assert session.timeout == 9.0
    assert client.handlers == [session._on_event]
    assert client.started is True


def test_init_registers_observer(make_session):
    def observer(record):
        pass

    client, session = make_session(observer=observer)
    assert client.handlers == [session._on_event, observer]


def test_observer_receives_streamed_events(make_session):
    seen = []
    _, session = make_session(observer=seen.append, events=settled_events("hi"))
    session.run("hi")
    assert any(r["type"] == "message_update" for r in seen)


def test_init_forwards_config_to_owned_client(make_session):
    client, _ = make_session(
        tools=["read"],
        model="claude",
        thinking="low",
        cwd="/tmp/x",
        persistent_session=True,
        session_dir="/tmp/x/.sessions",
    )
    assert client.config == {
        "tools": ["read"],
        "model": "claude",
        "thinking": "low",
        "cwd": "/tmp/x",
        "persistent_session": True,
        "session_dir": "/tmp/x/.sessions",
    }


def test_init_defaults_to_persistent_session(make_session):
    client, _ = make_session()
    assert client.config["persistent_session"] is True
    assert client.config["session_dir"] is None


def test_close_closes_owned_client(make_session):
    client, session = make_session()
    session.close()
    assert client.closed is True


def test_context_manager_closes_client(make_session):
    client, session = make_session()
    with session as entered:
        assert entered is session
        assert client.closed is False
    assert client.closed is True


# --------------------------------------------------------------------------- #
# Event collection
# --------------------------------------------------------------------------- #
def test_on_event_accumulates_text_deltas(make_session):
    _, session = make_session()
    session._on_event({"type": "message_update",
                       "assistantMessageEvent": {"type": "text_delta", "delta": "ab"}})
    session._on_event({"type": "message_update",
                       "assistantMessageEvent": {"type": "text_delta", "delta": "cd"}})
    assert session._parts == ["ab", "cd"]


def test_on_event_ignores_non_text_update(make_session):
    _, session = make_session()
    session._on_event({"type": "message_update",
                       "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"}})
    assert session._parts == []


def test_on_event_ignores_update_without_event_payload(make_session):
    _, session = make_session()
    session._on_event({"type": "message_update"})
    assert session._parts == []


def test_on_event_ignores_non_assistant_message_end(make_session):
    _, session = make_session()
    session._parts = ["keep"]
    session._on_event({"type": "message_end", "message": {"role": "user"}})
    assert session._parts == ["keep"]


def test_on_event_agent_settled_sets_event(make_session):
    _, session = make_session()
    session._on_event({"type": "agent_settled", "aborted": False})
    assert session._settled.is_set()


def test_on_event_ignores_unknown_type(make_session):
    _, session = make_session()
    session._on_event({"type": "agent_start"})
    assert session._parts == []
    assert session._settled.is_set() is False


# --------------------------------------------------------------------------- #
# Final-text replacement
# --------------------------------------------------------------------------- #
def test_replace_with_final_text_ignores_non_list_content(make_session):
    _, session = make_session()
    session._parts = ["keep"]
    session._replace_with_final_text({"content": "not a list"})
    assert session._parts == ["keep"]


def test_replace_with_final_text_empty_content_list_keeps_deltas(make_session):
    _, session = make_session()
    session._parts = ["keep"]
    session._replace_with_final_text({"content": []})
    assert session._parts == ["keep"]


def test_replace_with_final_text_skips_non_dict_and_non_text_blocks(make_session):
    _, session = make_session()
    session._replace_with_final_text({"content": [
        "not-a-dict",
        {"type": "thinking", "text": "hmm"},
        {"type": "text", "text": "final"},
    ]})
    assert session._parts == ["final"]


def test_replace_with_final_text_all_empty_text_keeps_deltas(make_session):
    _, session = make_session()
    session._parts = ["keep"]
    session._replace_with_final_text({"content": [{"type": "text", "text": ""}]})
    assert session._parts == ["keep"]


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
def test_run_returns_collected_text(make_session):
    client, session = make_session(events=settled_events("hello"))
    assert session.run("do it") == "hello"
    assert client.prompt_calls == [("do it", False, False)]


def test_run_passes_steer_and_follow_up(make_session):
    client, session = make_session(events=settled_events("ok"))
    session.run("x", steer=True, follow_up=True)
    assert client.prompt_calls == [("x", True, True)]


def test_run_raises_when_prompt_rejected_with_error(make_session):
    client, session = make_session(response={"success": False, "error": "rejected"})
    with pytest.raises(PiError, match="rejected"):
        session.run("x")


def test_run_raises_when_prompt_rejected_without_error(make_session):
    client, session = make_session(response={"success": False})
    with pytest.raises(PiError, match="prompt rejected"):
        session.run("x")


def test_run_raises_on_timeout(make_session):
    client, session = make_session(events=[], timeout=0.05)
    with pytest.raises(PiError, match="timed out"):
        session.run("x")


def test_run_explicit_timeout_override(make_session):
    client, session = make_session(events=[], timeout=5.0)
    with pytest.raises(PiError, match="timed out"):
        session.run("x", timeout=0.05)


def test_run_raises_when_pi_dies_before_the_turn_settles(make_session):
    """A Pi that cannot settle anymore fails the run, it does not wait it out."""
    client, session = make_session(events=[], timeout=30.0)
    client.die_after_prompt = True

    started = time.monotonic()
    with pytest.raises(PiError, match="pi exited before the turn settled"):
        session.run("x")
    elapsed = time.monotonic() - started

    assert elapsed < 10  # the 30s timeout never became the ending


def test_run_clears_previous_state(make_session):
    client, session = make_session(events=settled_events("second"))
    session.run("first")
    assert session.run("second") == "second"
    assert client.prompt_calls == [("first", False, False), ("second", False, False)]


# --------------------------------------------------------------------------- #
# retry()
# --------------------------------------------------------------------------- #
def test_retry_sends_corrective_message_in_same_session(make_session):
    client, session = make_session(events=settled_events("{}"))
    result = session.retry()
    assert result == "{}"
    message, steer, follow_up = client.prompt_calls[-1]
    assert "not valid JSON" in message
    assert steer is False and follow_up is False  # plain prompt, same session


def test_retry_includes_parser_error_when_given(make_session):
    client, session = make_session(events=settled_events("{}"))
    session.retry(error="boom")
    assert "boom" in client.prompt_calls[-1][0]


def test_retry_uses_custom_message_when_given(make_session):
    client, session = make_session(events=settled_events("{}"))
    session.retry(message="those files do not exist")
    message = client.prompt_calls[-1][0]
    assert message == "those files do not exist"


# --------------------------------------------------------------------------- #
# Integration against the fake `pi` subprocess (real PiClient)
# --------------------------------------------------------------------------- #
def test_run_against_real_client(fake_pi):
    session = PiSession()
    try:
        assert session.run("hello there") == "hello"
    finally:
        session.close()


def test_retry_against_real_client(fake_pi):
    session = PiSession()
    try:
        session.run("first")
        assert session.retry(error="boom") == "hello"
    finally:
        session.close()
