"""Tests for the PrimitiveManager registry."""

from __future__ import annotations

import pytest

import roles.primitive
from roles.primitive import Primitive
from roles.primitive_manager import PrimitiveManager


class StubSession:
    """Minimal stand-in for the gated Pi session a primitive opens."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def stub_sessions(monkeypatch):
    """Registering a primitive must not spawn a real Pi subprocess."""
    created: list[StubSession] = []

    def make_session(**kwargs):
        session = StubSession()
        created.append(session)
        return session

    monkeypatch.setattr(roles.primitive, "GatedPiSession", make_session)
    return created


def test_create_registers_and_returns(stub_sessions):
    manager = PrimitiveManager()

    primitive = manager.create("src/app.py")

    assert primitive == Primitive("src/app.py")
    assert manager.primitive_map["src/app.py"] is primitive
    assert primitive.session in stub_sessions


def test_create_defaults_dep_files():
    manager = PrimitiveManager()
    assert manager.create("src/app.py").dep_files == {}


def test_create_returns_existing_primitive_without_reattaching(stub_sessions):
    manager = PrimitiveManager()
    first = manager.create("a.py")

    second = manager.create("a.py")

    assert second is first
    assert manager.search("a.py") is first
    assert second.session is first.session
    # the existing primitive kept the session opened with it: no second
    # subprocess was spawned for the rejected registration
    assert len(stub_sessions) == 1


def test_search_returns_registered_or_none():
    manager = PrimitiveManager()
    manager.create("a.py")

    assert manager.search("a.py") is not None
    assert manager.search("missing.py") is None


def test_remove_reports_whether_one_was_registered():
    manager = PrimitiveManager()
    manager.create("a.py")

    assert manager.remove("a.py") is True
    assert manager.remove("a.py") is False
    assert manager.search("a.py") is None


def test_close_closes_every_primitive_session(stub_sessions):
    manager = PrimitiveManager()
    manager.create("a.py")
    manager.create("b.py")

    manager.close()

    assert [session.closed for session in stub_sessions] == [True, True]
    assert [primitive.session for primitive in manager.primitive_map.values()] == [
        None,
        None,
    ]
