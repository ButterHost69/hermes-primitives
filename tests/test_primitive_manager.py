"""Tests for the PrimitiveManager registry."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import roles.primitive
import roles.primitive_manager
from agent import PiError
from agent.gated_client import GatedPiSession
from roles.primitive import Primitive
from utils.json_utils import JsonCheck
from roles.primitive_manager import (
    UNSTATED_OBJECTIVE,
    PrimitiveCloseError,
    PrimitiveManager,
    PrimitiveRunError,
)

# Slack for a stub run that is only unblocked by another run concurrently.
_EVENT_TIMEOUT = 5.0


class StubSession:
    """Minimal stand-in for the gated Pi session a primitive opens.

    The hooks shape a run without a model: ``barrier`` only lets two runs
    through together, ``wait_event`` and ``set_event`` order them, ``delay``
    stretches one out, and ``error`` fails one.  ``closed_mid_run`` records a
    session that was closed while its run was still in flight — the one
    thing no caller of the manager is allowed to do.
    """

    def __init__(self):
        self.closed = False
        self.prompts: list[str] = []
        self.barrier: threading.Barrier | None = None
        self.wait_event: threading.Event | None = None
        self.set_event: threading.Event | None = None
        self.delay = 0.0
        self.error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.closed_mid_run = False

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.barrier is not None:
            self.barrier.wait()
        if self.wait_event is not None:
            self.wait_event.wait(timeout=_EVENT_TIMEOUT)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
        finally:
            if self.closed:
                self.closed_mid_run = True
            if self.set_event is not None:
                self.set_event.set()
        return f"report: {prompt}"

    def retry(self, *args, **kwargs) -> str:
        return "report: retry"

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


@pytest.fixture(autouse=True)
def stub_sessions(monkeypatch):
    """Registering or calling a primitive must not spawn a real Pi subprocess."""
    created: list[StubSession] = []

    def make_session(**kwargs):
        session = StubSession()
        created.append(session)
        return session

    monkeypatch.setattr(roles.primitive, "GatedPiSession", make_session)
    monkeypatch.setattr(
        roles.primitive,
        "verify_json",
        lambda text: JsonCheck(ok=True, value={}, error=None),
    )
    return created


def test_create_primitive_registers_and_returns(stub_sessions):
    manager = PrimitiveManager()

    primitive = manager.create_primitive("src/app.py")

    assert primitive == Primitive("src/app.py")
    assert manager.primitive_map["src/app.py"] is primitive
    assert primitive.session in stub_sessions


def test_create_primitive_defaults_dep_files():
    manager = PrimitiveManager()
    assert manager.create_primitive("src/app.py").dep_files == {}


def test_create_primitive_configures_the_session(stub_sessions):
    manager = PrimitiveManager()
    observer = lambda record: None  # noqa: E731

    manager.create_primitive(
        "src/app.py", model="claude", thinking="low", cwd="/repo", observer=observer
    )

    primitive = manager.search("src/app.py")
    assert (primitive.model, primitive.thinking) == ("claude", "low")
    assert (primitive.cwd, primitive.observer) == ("/repo", observer)


def test_create_primitive_returns_existing_primitive_without_reattaching(stub_sessions):
    manager = PrimitiveManager()
    first = manager.create_primitive("a.py")

    second = manager.create_primitive("a.py", model="claude")

    assert second is first
    assert manager.search("a.py") is first
    assert second.session is first.session
    # the existing primitive kept the session opened with it: no second
    # subprocess was spawned for the rejected registration
    assert len(stub_sessions) == 1
    assert first.model is None


def test_create_all_registers_every_path(stub_sessions):
    manager = PrimitiveManager()

    primitives = manager.create_all(["a.py", "b.py"])

    assert [primitive.source_file for primitive in primitives] == ["a.py", "b.py"]
    assert list(manager.primitive_map) == ["a.py", "b.py"]
    assert len(stub_sessions) == 2


def test_create_all_reuses_an_already_registered_path(stub_sessions):
    manager = PrimitiveManager()
    first = manager.create_primitive("a.py")

    primitives = manager.create_all(["a.py", "a.py", "b.py"])

    assert primitives[0] is first
    assert primitives[1] is first
    assert primitives[2] is not first
    assert len(stub_sessions) == 2


def test_create_all_with_no_paths_registers_nothing(stub_sessions):
    manager = PrimitiveManager()

    assert manager.create_all([]) == []
    assert manager.primitive_map == {}


def test_search_returns_registered_or_none():
    manager = PrimitiveManager()
    manager.create_primitive("a.py")

    assert manager.search("a.py") is not None
    assert manager.search("missing.py") is None


def test_remove_reports_whether_one_was_registered():
    manager = PrimitiveManager()
    manager.create_primitive("a.py")

    assert manager.remove("a.py") is True
    assert manager.remove("a.py") is False
    assert manager.search("a.py") is None


def test_call_primitive_runs_the_registered_primitive_on_its_objective():
    manager = PrimitiveManager({"a.py": "fix the crash"})
    primitive = manager.create_primitive("a.py")

    report = manager.call_primitive("a.py")

    assert report is not None
    # the objective travels inside the briefing the session is handed
    assert "Objective:\nfix the crash" in report
    assert len(primitive.session.prompts) == 1


def test_call_primitive_without_an_objective_runs_the_stated_one():
    manager = PrimitiveManager()
    primitive = manager.create_primitive("a.py")

    report = manager.call_primitive("a.py")

    assert report is not None
    assert f"Objective:\n{UNSTATED_OBJECTIVE}" in report
    assert len(primitive.session.prompts) == 1


def test_call_primitive_without_a_primitive_returns_none():
    manager = PrimitiveManager()

    assert manager.call_primitive("missing.py") is None
    assert manager.primitive_map == {}


def test_call_all_runs_every_registered_primitive():
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    first = manager.create_primitive("a.py")
    second = manager.create_primitive("b.py")
    first_session, second_session = first.session, second.session

    reports = manager.call_all()

    assert list(reports) == ["a.py", "b.py"]
    assert all(report is not None for report in reports.values())
    assert len(first_session.prompts) == 1
    assert len(second_session.prompts) == 1
    assert f"Objective:\nfix a" in first_session.prompts[0]
    assert f"Objective:\nfix b" in second_session.prompts[0]


def test_call_all_with_an_empty_registry_returns_nothing():
    manager = PrimitiveManager()

    assert manager.call_all() == {}


def test_call_all_runs_the_primitives_concurrently(stub_sessions):
    """A barrier two runs must meet proves the fan-out: serial runs time out."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py")
    manager.create_primitive("b.py")
    barrier = threading.Barrier(2, timeout=5.0)
    for primitive in manager.primitive_map.values():
        primitive.session.barrier = barrier

    reports = manager.call_all(max_workers=100)

    assert set(reports) == {"a.py", "b.py"}
    assert all(session.closed for session in stub_sessions)


def test_call_all_with_max_workers_one_does_not_fan_out(stub_sessions):
    """The barrier cannot be met by runs that share a single worker, one by one."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py")
    manager.create_primitive("b.py")
    barrier = threading.Barrier(2, timeout=1.0)
    for primitive in manager.primitive_map.values():
        primitive.session.barrier = barrier

    with pytest.raises(PrimitiveRunError) as excinfo:
        manager.call_all(max_workers=1)

    assert set(excinfo.value.failures) == {"a.py", "b.py"}
    assert list(excinfo.value.failures) == ["a.py", "b.py"]
    # the failures are broken barriers, and every session was still closed
    assert all(
        isinstance(exc, threading.BrokenBarrierError)
        for exc in excinfo.value.failures.values()
    )
    assert all(session.closed for session in stub_sessions)


def test_call_all_returns_reports_in_registration_order(stub_sessions):
    """The slowest primitive registered first does not reshuffle the result."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    slow = manager.create_primitive("a.py")
    fast = manager.create_primitive("b.py")
    fast_done = threading.Event()
    fast.session.set_event = fast_done
    slow.session.wait_event = fast_done
    slow.session.delay = 0.05

    reports = manager.call_all()

    assert list(reports) == ["a.py", "b.py"]


def test_call_all_collects_failures_as_they_come(stub_sessions):
    """A failure is handed over in completion order, like a report."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    slow = manager.create_primitive("a.py")
    fast = manager.create_primitive("b.py")
    fast_done = threading.Event()
    fast.session.set_event = fast_done
    fast.session.error = PiError("boom")
    slow.session.wait_event = fast_done
    slow.session.delay = 0.05
    failures: list[tuple[str, str]] = []

    with pytest.raises(PrimitiveRunError):
        manager.call_all(
            on_failure=lambda source_file, exc: failures.append((source_file, str(exc)))
        )

    assert failures == [("b.py", "boom")]


def test_call_all_collects_reports_as_they_come(stub_sessions):
    """A report is handed over the completion order, not the registry order."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    slow = manager.create_primitive("a.py")
    fast = manager.create_primitive("b.py")
    fast_done = threading.Event()
    fast.session.set_event = fast_done
    slow.session.wait_event = fast_done
    slow.session.delay = 0.05
    arrived: list[str] = []

    reports = manager.call_all(on_report=lambda source_file, report: arrived.append(source_file))

    assert arrived == ["b.py", "a.py"]
    assert list(reports) == ["a.py", "b.py"]


def test_call_all_raises_but_keeps_sibling_reports_when_a_primitive_fails(stub_sessions):
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    good = manager.create_primitive("a.py")
    failing = manager.create_primitive("b.py")
    failed = threading.Event()
    failing_session, good_session = failing.session, good.session
    failing_session.error = PiError("boom")
    failing_session.set_event = failed
    good_session.wait_event = failed

    with pytest.raises(PrimitiveRunError) as excinfo:
        manager.call_all()

    error = excinfo.value
    assert set(error.failures) == {"b.py"}
    assert isinstance(error.failures["b.py"], PiError)
    assert set(error.reports) == {"a.py"}
    assert "report: " in error.reports["a.py"]
    # the passing sibling finished before anything was closed, and every
    # session was closed exactly when the fan-out ended
    assert not good_session.closed_mid_run
    assert all(session.closed for session in stub_sessions)


def test_call_all_rejects_a_max_workers_below_one():
    manager = PrimitiveManager()
    manager.create_primitive("a.py")

    with pytest.raises(ValueError):
        manager.call_all(max_workers=0)


def test_call_all_rejects_a_max_workers_below_one_even_when_empty():
    """The bad argument is the caller's error, before any registry is walked."""
    manager = PrimitiveManager()

    with pytest.raises(ValueError):
        manager.call_all(max_workers=0)


# --------------------------------------------------------------------------- #
# Unexpected scenarios: crashes, refusals, and teardown that fights back
# --------------------------------------------------------------------------- #
def test_close_keeps_going_when_a_session_refuses_to_close(stub_sessions):
    """One stubborn session must not strand the sessions behind it."""
    manager = PrimitiveManager()
    manager.create_primitive("a.py")
    stubborn = manager.create_primitive("b.py")
    stubborn.session.close_error = OSError("terminate denied")

    with pytest.raises(PrimitiveCloseError) as excinfo:
        manager.close()

    assert set(excinfo.value.failures) == {"b.py"}
    # the healthy session was closed even though the stubborn one sat in front
    assert stub_sessions[0].closed is True


def test_call_all_closes_the_registry_when_one_close_fails(stub_sessions):
    """The fan-out holds its cleanup half of the deal on a close failure."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py")
    stubborn = manager.create_primitive("b.py")
    stubborn.session.close_error = OSError("terminate denied")

    with pytest.raises(PrimitiveCloseError) as excinfo:
        manager.call_all()

    assert isinstance(excinfo.value.failures["b.py"], OSError)
    # the sibling behind the stubborn session was still closed
    assert stub_sessions[0].closed is True


def test_call_all_keeps_the_run_failure_when_a_close_also_fails(stub_sessions):
    """A close failure must not hide which primitive failed the run."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    good = manager.create_primitive("a.py")
    good_session = good.session
    failing = manager.create_primitive("b.py")
    failing.session.error = PiError("the agent died")
    failing.session.close_error = OSError("terminate denied")

    with pytest.raises(PrimitiveRunError) as excinfo:
        manager.call_all()

    error = excinfo.value
    assert set(error.failures) == {"b.py"}
    assert isinstance(error.failures["b.py"], PiError)
    # the close failure travels with the run failure, chained onto it
    assert isinstance(error.__context__, PrimitiveCloseError)
    assert good_session.closed is True


def test_call_all_closes_the_registry_when_a_submission_is_refused(stub_sessions, monkeypatch):
    """An OS that refuses a thread must not leak the sessions it already made."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py")
    manager.create_primitive("b.py")
    submissions = 0

    def refusing_pool(*, max_workers, thread_name_prefix):
        pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=thread_name_prefix
        )
        submit = pool.submit

        def refuse_the_second(fn, *args, **kwargs):
            nonlocal submissions
            submissions += 1
            if submissions == 2:
                raise RuntimeError("can't start new thread")
            return submit(fn, *args, **kwargs)

        pool.submit = refuse_the_second
        return pool

    monkeypatch.setattr(roles.primitive_manager, "ThreadPoolExecutor", refusing_pool)

    with pytest.raises(RuntimeError, match="can't start new thread"):
        manager.call_all()

    assert submissions == 2
    # the one submission that landed still ran, and every session was closed
    assert stub_sessions[0].prompts
    assert all(session.closed for session in stub_sessions)


def test_call_all_closes_the_registry_when_on_report_raises(stub_sessions):
    """A broken observer breaks its caller, not the cleanup around it."""
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py")
    manager.create_primitive("b.py")

    def broken_observer(source_file, report):
        raise RuntimeError("the observer is broken")

    with pytest.raises(RuntimeError, match="the observer is broken"):
        manager.call_all(on_report=broken_observer)

    # every sibling ran to completion before the fan-out unwound, and every
    # session was closed
    assert all(session.prompts for session in stub_sessions)
    assert all(session.closed for session in stub_sessions)


def test_call_all_on_a_closed_registry_fails_every_path(stub_sessions):
    """A second fan-out must not resurrect a session or pretend it succeeded."""
    manager = PrimitiveManager({"a.py": "fix a"})
    manager.create_primitive("a.py")

    assert set(manager.call_all()) == {"a.py"}

    with pytest.raises(PrimitiveRunError) as excinfo:
        manager.call_all()

    assert "this primitive is closed" in str(excinfo.value.failures["a.py"])
    assert stub_sessions[0].closed is True


def test_call_all_fails_the_fan_out_when_the_agents_die_mid_turn(
    crash_mid_turn_pi, tmp_path, monkeypatch
):
    """A Pi that dies mid-turn fails fast, inside a real fan-out.

    Every primitive drives its own real Pi subprocess here, so the run is a
    watchdog away from holding the room for the 120s session timeout if the
    session ever stops noticing the death.
    """
    # the module's stub sessions are not what this scenario is about: put the
    # real gated session back so the fan-out spawns actual Pi subprocesses
    monkeypatch.setattr(roles.primitive, "GatedPiSession", GatedPiSession)
    manager = PrimitiveManager({"a.py": "fix a", "b.py": "fix b"})
    manager.create_primitive("a.py", cwd=str(tmp_path))
    manager.create_primitive("b.py", cwd=str(tmp_path))
    outcome: dict = {}

    def fan_out() -> None:
        started = time.monotonic()
        try:
            manager.call_all()
        except BaseException as exc:  # noqa: BLE001 - reported through outcome
            outcome["error"] = exc
        outcome["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=fan_out, daemon=True)
    worker.start()
    worker.join(timeout=30.0)
    if worker.is_alive():
        pytest.fail("the fan-out is blocked on agents that are already dead")

    error = outcome.get("error")
    assert isinstance(error, PrimitiveRunError), f"expected PrimitiveRunError, got {outcome!r}"
    assert set(error.failures) == {"a.py", "b.py"}
    assert all("pi exited" in str(exc) for exc in error.failures.values())
    assert outcome["elapsed"] < 10  # the 120s session timeout was never the ending
    # no agent outlived the fan-out: every session is closed
    assert all(p.session is None for p in manager.primitive_map.values())



def test_close_closes_every_primitive_session(stub_sessions):
    manager = PrimitiveManager()
    manager.create_primitive("a.py")
    manager.create_primitive("b.py")

    manager.close()

    assert [session.closed for session in stub_sessions] == [True, True]
    assert [primitive.session for primitive in manager.primitive_map.values()] == [
        None,
        None,
    ]
