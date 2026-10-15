"""Tests for the PrimitiveManager registry."""

from __future__ import annotations

import pytest

import roles.primitive
from roles.primitive import Primitive
from roles.primitive_manager import UNSTATED_OBJECTIVE, PrimitiveManager


class StubSession:
    """Minimal stand-in for the gated Pi session a primitive opens."""

    def __init__(self):
        self.closed = False
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"report: {prompt}"

    def close(self):
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

    reports = manager.call_all()

    assert list(reports) == ["a.py", "b.py"]
    assert all(report is not None for report in reports.values())
    assert len(first.session.prompts) == 1
    assert len(second.session.prompts) == 1
    assert f"Objective:\nfix a" in first.session.prompts[0]
    assert f"Objective:\nfix b" in second.session.prompts[0]


def test_call_all_with_an_empty_registry_returns_nothing():
    manager = PrimitiveManager()

    assert manager.call_all() == {}


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
